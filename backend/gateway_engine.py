"""Gateway engine — bridges BACnet polling to MQTT with priority array support and MQTT command handling."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.bacnet_service import BacnetService
from backend.config_manager import ConfigManager
from backend.models import GatewayStatus, PointMapping
from backend.mqtt_service import MqttService
from backend.websocket_manager import WebSocketManager

try:
    from backend.anomaly_engine import AnomalyEngine
except ImportError:
    AnomalyEngine = None  # Graceful degradation if module missing

logger = logging.getLogger(__name__)


class GatewayEngine:
    """Orchestrates BACnet→MQTT polling, priority array reading, and MQTT command control."""

    def __init__(
        self,
        config_manager: ConfigManager,
        bacnet: BacnetService,
        mqtt: MqttService,
        ws_manager: WebSocketManager,
        history_store=None,
        webhook_service=None,
    ):
        self._cm = config_manager
        self._bacnet = bacnet
        self._mqtt = mqtt
        self._ws = ws_manager
        self._history = history_store
        self._webhook = webhook_service  # Optional WebhookService
        self._anomaly: "AnomalyEngine | None" = None  # Set by main.py after init

        self._status = GatewayStatus.STOPPED
        self._start_time: float | None = None
        self._polling_task: asyncio.Task[None] | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # COV: track last values for change detection
        self._last_cov_values: dict[str, Any] = {}

        # Device status tracking: { device_id: { online, fail_count, last_seen, last_fail } }
        self._device_status: dict[int, dict] = {}

        # Pinging: known devices loaded from persistence (survive restart)
        # { device_id: {address, name} }
        self._known_devices: dict[int, dict] = {}
        self._ping_task: asyncio.Task[None] | None = None
        self._devices_file = Path(__file__).resolve().parent.parent / "config" / "discovered_devices.json"
        self._load_known_devices()

        # BACnet read serializer — prevents concurrent reads saturating BAC0 queue
        # Critical for MSTP networks where only one request can be in-flight
        self._bacnet_read_lock = asyncio.Lock()

        # RAM health guardian
        self._ram_throttled = False
        self._ram_paused = False
        self._gc_counter = 0
        self._ram_check_counter = 0  # Check RAM every 120 cycles (~60s at 0.5s sleep)
        self._RAM_WARN_PCT = 80
        self._RAM_THROTTLE_PCT = 90
        self._RAM_PAUSE_PCT = 95

    # ── lifecycle ──────────────────────────────
    async def start(self) -> None:
        """Start the polling loop and subscribe to MQTT command topics."""
        if self._running:
            logger.warning("Gateway already running.")
            return

        self._running = True
        self._status = GatewayStatus.RUNNING
        self._start_time = time.time()
        self._loop = asyncio.get_running_loop()

        prefix = self._cm.config.mqtt.topic_prefix

        # Subscribe to command topics
        self._mqtt.subscribe(f"{prefix}/cmd/#", callback=self._handle_mqtt_command)

        self._polling_task = asyncio.create_task(self._polling_loop())
        self._ping_task = asyncio.create_task(self._device_ping_loop())
        logger.info("Gateway engine started. Listening on %s/cmd/#", prefix)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        for task in [self._polling_task, self._ping_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._status = GatewayStatus.STOPPED
        self._start_time = None
        self._loop = None
        logger.info("Gateway engine stopped.")

    @property
    def status(self) -> GatewayStatus:
        return self._status

    @property
    def uptime(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    # ═══════════════════════════════════════════
    # Polling Loop — reads presentValue + priorityArray
    # ═══════════════════════════════════════════
    async def _polling_loop(self) -> None:
        last_poll: dict[str, float] = {}

        while self._running:
            try:
                # RAM health check every ~60s (120 cycles × 0.5s) to avoid /proc/meminfo I/O spam
                self._ram_check_counter += 1
                if self._ram_check_counter >= 120:
                    self._ram_check_counter = 0
                    ram_ok = self._check_ram_health()
                    if not ram_ok:
                        await asyncio.sleep(5)  # Wait and re-check
                        continue

                # Periodic GC (every 60 cycles ≈ 30 seconds)
                self._gc_counter += 1
                if self._gc_counter >= 60:
                    gc.collect()
                    self._gc_counter = 0

                mappings = self._cm.mappings
                now = time.time()

                for mapping in mappings:
                    if not mapping.enabled:
                        continue

                    # COV mode: use longer base interval (60s refresh)
                    # Poll mode: use configured poll_interval
                    if mapping.read_mode == "cov":
                        interval = max(mapping.poll_interval, 60)
                    else:
                        interval = mapping.poll_interval

                    # Apply throttle multiplier if RAM is high
                    if self._ram_throttled:
                        interval = interval * 2  # Double interval under memory pressure

                    elapsed = now - last_poll.get(mapping.id, 0)
                    if elapsed < interval:
                        continue

                    last_poll[mapping.id] = now
                    # Schedule poll but serialized through _bacnet_read_lock
                    # This prevents BAC0 queue saturation on MSTP networks
                    asyncio.create_task(self._poll_single_locked(mapping))

                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Polling loop error: %s", exc)
                self._status = GatewayStatus.ERROR
                await asyncio.sleep(5)

    def _check_ram_health(self) -> bool:
        """Check RAM usage and manage throttling/pausing.

        Returns False if polling should be paused entirely.
        """
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()

            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    mem_info[key] = int(parts[1])  # kB

            total = mem_info.get('MemTotal', 1)
            available = mem_info.get('MemAvailable', total)
            used_pct = ((total - available) / total) * 100

            # PAUSE: critical memory (>95%)
            if used_pct >= self._RAM_PAUSE_PCT:
                if not self._ram_paused:
                    logger.critical(
                        "RAM CRITICAL: %.1f%% used (%d MB available). PAUSING all polling!",
                        used_pct, available // 1024,
                    )
                    gc.collect()
                    self._ram_paused = True
                    self._ram_throttled = True
                return False

            # THROTTLE: high memory (>90%)
            if used_pct >= self._RAM_THROTTLE_PCT:
                if not self._ram_throttled:
                    logger.warning(
                        "RAM HIGH: %.1f%% used (%d MB available). Throttling poll intervals x2.",
                        used_pct, available // 1024,
                    )
                    gc.collect()
                    self._ram_throttled = True
                self._ram_paused = False
                return True

            # WARN: elevated memory (>80%)
            if used_pct >= self._RAM_WARN_PCT:
                if not self._ram_throttled:  # Log once
                    logger.warning("RAM elevated: %.1f%% used (%d MB available)", used_pct, available // 1024)

            # RECOVER: back to normal
            if self._ram_throttled and used_pct < self._RAM_THROTTLE_PCT:
                logger.info(
                    "RAM recovered: %.1f%% used (%d MB available). Resuming normal polling.",
                    used_pct, available // 1024,
                )
                self._ram_throttled = False
                self._ram_paused = False

            return True

        except Exception:
            return True  # If /proc/meminfo fails, keep polling

    async def _poll_single_locked(self, mapping: PointMapping) -> None:
        """Wrapper that serializes BACnet reads through a shared lock.

        On MSTP networks only one request can be in-flight at a time.
        Without this lock, concurrent asyncio tasks would hammer BAC0's
        internal queue and cause cascading no-response errors.
        """
        async with self._bacnet_read_lock:
            await self._poll_single(mapping)

    async def _poll_single(self, mapping: PointMapping) -> None:
        """Read presentValue + priorityArray and publish to MQTT + WebSocket."""
        try:
            address = self._bacnet.get_device_address(mapping.device_id)
            if not address:
                self._on_device_poll_fail(mapping.device_id, "No address found")
                return

            # Read presentValue
            value = await self._bacnet.read_object(
                address, mapping.object_type, mapping.object_instance
            )

            # Read priorityArray (may be empty for non-commandable objects)
            priority_array = await self._bacnet.read_priority_array(
                address, mapping.object_type, mapping.object_instance
            )

            # Read eventState for alarm detection
            alarm_state = None
            try:
                alarm_state = await self._bacnet.read_event_state(
                    address, mapping.object_type, mapping.object_instance
                )
            except Exception:
                pass  # Not all objects support eventState

            now_str = datetime.now(timezone.utc).isoformat()

            # Update mapping state
            mapping.last_value = value
            mapping.last_updated = now_str
            if priority_array:
                mapping.priority_array = priority_array

            # Track device success
            self._on_device_poll_success(mapping.device_id, address)

            # Check alarm state changes
            if alarm_state and alarm_state != "normal":
                alarm_key = f"alarm:{mapping.id}"
                old_alarm = self._last_cov_values.get(alarm_key)
                if old_alarm != alarm_state:
                    self._last_cov_values[alarm_key] = alarm_state
                    severity = "critical" if alarm_state in ("fault", "high-limit", "low-limit") else "warning"
                    if self._history:
                        self._history.log_event(
                            "alarm", f"{mapping.label or mapping.id}: {alarm_state}",
                            device_id=mapping.device_id, mapping_id=mapping.id,
                            severity=severity, data={"alarm_state": alarm_state, "value": str(value)},
                        )
                    # Broadcast alarm via WebSocket
                    await self._ws.broadcast({
                        "type": "alarm",
                        "mapping_id": mapping.id,
                        "device_id": mapping.device_id,
                        "label": mapping.label,
                        "alarm_state": alarm_state,
                        "value": value,
                        "timestamp": now_str,
                    })
                    # Fire webhook (non-blocking)
                    if self._webhook:
                        msg = f"{mapping.label or mapping.id}: {alarm_state}"
                        asyncio.create_task(self._webhook.fire(
                            event_type="alarm_triggered",
                            severity=severity,
                            mapping_id=mapping.id,
                            label=mapping.label or mapping.id,
                            device_id=mapping.device_id,
                            object_type=mapping.object_type,
                            object_instance=mapping.object_instance,
                            value=value,
                            alarm_state=alarm_state,
                            message=msg,
                        ))
            elif alarm_state == "normal":
                # Clear alarm if it was set
                alarm_key = f"alarm:{mapping.id}"
                old_alarm = self._last_cov_values.get(alarm_key)
                if old_alarm and old_alarm != "normal":
                    self._last_cov_values[alarm_key] = "normal"
                    if self._history:
                        self._history.log_event(
                            "alarm", f"{mapping.label or mapping.id}: alarm cleared (normal)",
                            device_id=mapping.device_id, mapping_id=mapping.id,
                            severity="info", data={"alarm_state": "normal", "value": str(value)},
                        )

            # ── User-defined threshold alarm check ──────────
            acfg = mapping.alarm_config
            if acfg and acfg.enabled and value is not None:
                try:
                    fval = float(value)
                    thresh_key = f"thresh:{mapping.id}"
                    old_thresh = self._last_cov_values.get(thresh_key, "normal")
                    new_thresh = "normal"

                    if acfg.high_limit is not None and fval > acfg.high_limit:
                        new_thresh = "high-limit"
                    elif acfg.low_limit is not None and fval < acfg.low_limit:
                        new_thresh = "low-limit"
                    elif old_thresh != "normal":
                        # Apply deadband: only clear if value has returned within limits by deadband
                        if old_thresh == "high-limit" and acfg.high_limit is not None:
                            if fval > (acfg.high_limit - acfg.deadband):
                                new_thresh = old_thresh  # Still in alarm zone
                        elif old_thresh == "low-limit" and acfg.low_limit is not None:
                            if fval < (acfg.low_limit + acfg.deadband):
                                new_thresh = old_thresh  # Still in alarm zone

                    if new_thresh != old_thresh:
                        self._last_cov_values[thresh_key] = new_thresh
                        if new_thresh != "normal":
                            sev = acfg.severity or "warning"
                            msg = f"{mapping.label or mapping.id}: {new_thresh} (val={fval})"
                            if self._history:
                                self._history.log_event(
                                    "alarm", msg,
                                    device_id=mapping.device_id, mapping_id=mapping.id,
                                    severity=sev,
                                    data={"alarm_state": new_thresh, "value": str(fval),
                                          "threshold": str(acfg.high_limit if new_thresh == "high-limit" else acfg.low_limit)},
                                )
                            await self._ws.broadcast({
                                "type": "alarm",
                                "mapping_id": mapping.id,
                                "device_id": mapping.device_id,
                                "label": mapping.label,
                                "alarm_state": new_thresh,
                                "value": fval,
                                "timestamp": now_str,
                                "source": "threshold",
                            })
                            # Fire webhook (non-blocking)
                            if self._webhook:
                                asyncio.create_task(self._webhook.fire(
                                    event_type="threshold_breach",
                                    severity=sev,
                                    mapping_id=mapping.id,
                                    label=mapping.label or mapping.id,
                                    device_id=mapping.device_id,
                                    object_type=mapping.object_type,
                                    object_instance=mapping.object_instance,
                                    value=fval,
                                    alarm_state=new_thresh,
                                    message=msg,
                                ))
                        else:
                            # Alarm cleared
                            if self._history:
                                self._history.log_event(
                                    "alarm", f"{mapping.label or mapping.id}: threshold alarm cleared (val={fval})",
                                    device_id=mapping.device_id, mapping_id=mapping.id,
                                    severity="info",
                                    data={"alarm_state": "normal", "value": str(fval)},
                                )
                except (ValueError, TypeError):
                    pass  # Value not numeric, skip threshold check

            # ── Scenario Anomaly Monitor ────────────────
            if self._anomaly is not None:
                try:
                    asyncio.create_task(self._anomaly.evaluate(mapping.id, value))
                except Exception as _ae:
                    logger.debug("Anomaly evaluate error: %s", _ae)

            # Build MQTT topics
            prefix = self._cm.config.mqtt.topic_prefix
            base_topic = mapping.mqtt_topic
            if not base_topic:
                base_topic = f"{prefix}/{mapping.device_id}/{mapping.object_type}/{mapping.object_instance}"

            # Publish presentValue
            value_payload = {
                "value": value,
                "object_type": mapping.object_type,
                "object_instance": mapping.object_instance,
                "device_id": mapping.device_id,
                "timestamp": now_str,
            }
            if alarm_state:
                value_payload["alarm_state"] = alarm_state
            self._mqtt.publish(f"{base_topic}/value", value_payload)

            # Record to history DB
            if self._history:
                try:
                    self._history.record(mapping.id, value)
                except Exception as he:
                    logger.debug("History record error: %s", he)

            # Publish priorityArray (if available)
            if priority_array:
                pa_payload = {
                    "present_value": value,
                    "priority_array": priority_array,
                    "object_type": mapping.object_type,
                    "object_instance": mapping.object_instance,
                    "device_id": mapping.device_id,
                    "timestamp": now_str,
                }
                self._mqtt.publish(f"{base_topic}/priority_array", pa_payload)

            # COV mode: only publish when value changes
            if mapping.read_mode == "cov":
                old_val = self._last_cov_values.get(mapping.id)
                if old_val == value:
                    return  # No change — skip publish
                self._last_cov_values[mapping.id] = value

            # Broadcast to WebSocket clients
            ws_msg = {
                "type": "point_update",
                "mapping_id": mapping.id,
                "label": mapping.label,
                "read_mode": mapping.read_mode,
                "priority_array": priority_array if priority_array else None,
                **value_payload,
            }
            await self._ws.broadcast(ws_msg)

        except Exception as exc:
            self._on_device_poll_fail(mapping.device_id, str(exc))
            logger.error("Poll error for mapping %s: %s", mapping.id, exc)

    # ── Device Status Tracking ─────────────────
    def _on_device_poll_success(self, device_id: int, address: str) -> None:
        """Mark a device as successfully polled."""
        status = self._device_status.setdefault(device_id, {
            "online": False, "fail_count": 0, "last_seen": None, "last_fail": None, "address": None,
        })
        was_offline = not status["online"]
        status["fail_count"] = 0
        status["online"] = True
        status["last_seen"] = datetime.now(timezone.utc).isoformat()
        status["address"] = address

        if was_offline:
            logger.info("Device %d came ONLINE at %s", device_id, address)
            if self._history:
                self._history.log_event(
                    "device_online", f"Device {device_id} is online at {address}",
                    device_id=device_id, severity="info",
                    data={"address": address},
                )
            # Broadcast status change
            asyncio.ensure_future(self._ws.broadcast({
                "type": "device_status",
                "device_id": device_id,
                "online": True,
                "address": address,
                "timestamp": status["last_seen"],
            }))
            prefix = self._cm.config.mqtt.topic_prefix
            self._mqtt.publish(f"{prefix}/{device_id}/status", {"online": True, "address": address})

    def _on_device_poll_fail(self, device_id: int, error: str) -> None:
        """Record a poll failure for a device."""
        status = self._device_status.setdefault(device_id, {
            "online": True, "fail_count": 0, "last_seen": None, "last_fail": None, "address": None,
        })
        status["fail_count"] += 1
        status["last_fail"] = datetime.now(timezone.utc).isoformat()

        # Transition to offline after 3 consecutive failures
        if status["online"] and status["fail_count"] >= 3:
            status["online"] = False
            logger.warning("Device %d went OFFLINE (3 consecutive failures): %s", device_id, error)
            if self._history:
                self._history.log_event(
                    "device_offline", f"Device {device_id} offline after 3 poll failures: {error}",
                    device_id=device_id, severity="warning",
                    data={"error": error, "fail_count": status["fail_count"]},
                )
            asyncio.ensure_future(self._ws.broadcast({
                "type": "device_status",
                "device_id": device_id,
                "online": False,
                "timestamp": status["last_fail"],
            }))
            prefix = self._cm.config.mqtt.topic_prefix
            self._mqtt.publish(f"{prefix}/{device_id}/status", {"online": False, "error": error})

    def get_device_status(self) -> dict[int, dict]:
        """Return current device status dict."""
        return dict(self._device_status)

    # ── Known device registry (persist across restarts) ──
    def _load_known_devices(self) -> None:
        """Load persisted discovered devices from JSON file."""
        try:
            if self._devices_file.exists():
                with open(self._devices_file, "r") as f:
                    data = json.load(f)
                self._known_devices = {int(k): v for k, v in data.items()}
                logger.info("Loaded %d known devices from %s", len(self._known_devices), self._devices_file)
        except Exception as e:
            logger.warning("Could not load known devices: %s", e)
            self._known_devices = {}

    def _save_known_devices(self) -> None:
        """Persist known devices to JSON file."""
        try:
            self._devices_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._devices_file, "w") as f:
                json.dump({str(k): v for k, v in self._known_devices.items()}, f, indent=2)
        except Exception as e:
            logger.warning("Could not save known devices: %s", e)

    def register_discovered_devices(self, devices: list) -> None:
        """Called after discovery to register all discovered devices so they are tracked and persisted."""
        changed = False
        for dev in devices:
            dev_id = dev.device_id
            existing = self._known_devices.get(dev_id)
            new_entry = {
                "device_id": dev_id,
                "name": dev.device_name or f"Device {dev_id}",
                "address": dev.address or "",
            }
            if existing != new_entry:
                self._known_devices[dev_id] = new_entry
                changed = True
        if changed:
            self._save_known_devices()
            logger.info("Registered %d known devices (total: %d)", len(devices), len(self._known_devices))

    def get_known_devices(self) -> dict[int, dict]:
        """Return all known devices (survived restarts)."""
        return dict(self._known_devices)

    # ── Background device ping loop ──────────────
    async def _device_ping_loop(self) -> None:
        """
        Periodically 'ping' all known devices by reading objectName.
        - Syncs from BAC0 cache every SYNC_INTERVAL_S to pick up new devices
        - Saves real device name when objectName read succeeds
        - Updates online/offline WITHOUT requiring mappings
        """
        PING_INTERVAL_S = 180
        PING_FAIL_THRESHOLD = 3
        STAGGER_S = 2.0
        SYNC_INTERVAL_S = 30    # sync from BAC0 cache every 30s

        last_pinged: dict[int, float] = {}
        ping_fails: dict[int, int] = {}
        last_sync = 0.0

        logger.info("[Ping] Device ping loop started (%d known devices)", len(self._known_devices))

        while self._running:
            try:
                if not self._bacnet.connected:
                    await asyncio.sleep(10)
                    continue

                now = time.monotonic()

                # ── Periodically sync from BAC0 full device cache ────────────
                # BAC0 accumulates I-Am responses continuously; this picks up
                # devices discovered after our initial API scan
                if now - last_sync >= SYNC_INTERVAL_S:
                    last_sync = now
                    changed = False
                    for dev in self._bacnet.discovered_devices:
                        did = dev.device_id
                        if did not in self._known_devices:
                            self._known_devices[did] = {
                                "device_id": did,
                                "name": dev.device_name or f"Device {did}",
                                "address": dev.address or "",
                            }
                            changed = True
                        else:
                            entry = self._known_devices[did]
                            if not entry.get("address") and dev.address:
                                entry["address"] = dev.address
                                changed = True
                            # Accept real name from BAC0 cache if available
                            cached = dev.device_name
                            if cached and cached != f"Device {did}":
                                cur = entry.get("name", "")
                                if not cur or cur == f"Device {did}":
                                    entry["name"] = cached
                                    changed = True
                    if changed:
                        self._save_known_devices()
                        logger.info("[Ping] Synced; total known: %d", len(self._known_devices))

                # ── Ping devices that are due ────────────────────────────────
                for dev_id, dev_info in list(self._known_devices.items()):
                    elapsed = now - last_pinged.get(dev_id, 0)
                    if elapsed < PING_INTERVAL_S:
                        continue

                    address = dev_info.get("address", "")
                    if not address:
                        bdev = self._bacnet.get_device(dev_id)
                        if bdev:
                            address = bdev.address
                            dev_info["address"] = address
                    if not address:
                        continue

                    last_pinged[dev_id] = now

                    # Read objectName — ping + name resolution in one
                    name_result = None
                    try:
                        name_result = await self._bacnet.read_object(
                            address, "device", dev_id, "objectName"
                        )
                        success = name_result is not None
                    except Exception:
                        success = False

                    if success:
                        ping_fails[dev_id] = 0
                        # Update real name if we got one
                        if name_result:
                            name_str = str(name_result).strip()
                            cur = dev_info.get("name", "")
                            if name_str and name_str != f"Device {dev_id}" and (not cur or cur == f"Device {dev_id}"):
                                dev_info["name"] = name_str
                                self._save_known_devices()
                        # Mark ONLINE — always update on success
                        self._on_device_poll_success(dev_id, address)
                    else:
                        # DO NOT mark offline from ping — failure could be MSTP route not cached,
                        # temporary timeout, etc. Only the poll loop marks devices offline.
                        # If device has never been polled (no mappings), leave status as None (pending).
                        ping_fails[dev_id] = ping_fails.get(dev_id, 0) + 1
                        # Log repeated failures for debugging only
                        if ping_fails.get(dev_id, 0) == 5:
                            logger.debug("[Ping] Device %d at %s: 5 consecutive ping failures", dev_id, address)

                    await asyncio.sleep(STAGGER_S)

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[Ping] Loop error: %s", exc)
                await asyncio.sleep(10)

        logger.info("[Ping] Device ping loop stopped")

    # ═══════════════════════════════════════════
    # MQTT Command Handler
    # ═══════════════════════════════════════════
    def _handle_mqtt_command(self, topic: str, payload: Any) -> None:
        """Route incoming MQTT commands (runs in MQTT thread → schedules on event loop)."""
        try:
            parts = topic.split("/")
            # Find 'cmd' in topic parts
            try:
                idx = parts.index("cmd")
            except ValueError:
                return

            command = parts[idx + 1] if len(parts) > idx + 1 else ""

            if command == "write":
                self._schedule(self._cmd_write(parts, idx, payload))
            elif command == "release":
                self._schedule(self._cmd_release(parts, idx, payload))
            elif command == "add_point":
                self._schedule(self._cmd_add_point(payload))
            elif command == "remove_point":
                self._schedule(self._cmd_remove_point(payload))
            elif command == "list_points":
                self._schedule(self._cmd_list_points())
            else:
                logger.warning("Unknown MQTT command: %s", command)

        except Exception as exc:
            logger.error("MQTT command parse error on %s: %s", topic, exc)

    def _schedule(self, coro: Any) -> None:
        """Schedule an async coroutine on the main event loop from the MQTT thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _publish_response(self, command: str, success: bool, message: str, extra: dict | None = None) -> None:
        """Publish a response on <prefix>/response/<command>."""
        prefix = self._cm.config.mqtt.topic_prefix
        payload: dict[str, Any] = {"success": success, "message": message}
        if extra:
            payload.update(extra)
        self._mqtt.publish(f"{prefix}/response/{command}", payload)

    # ── cmd/write ──────────────────────────────
    async def _cmd_write(self, parts: list[str], idx: int, payload: Any) -> None:
        """Handle: <prefix>/cmd/write/<device_id>/<object_type>/<instance>
        Payload: {"value": <val>, "priority": <8-16>}
        """
        try:
            device_id = int(parts[idx + 2])
            object_type = parts[idx + 3]
            object_instance = int(parts[idx + 4])

            if isinstance(payload, dict):
                value = payload.get("value")
                priority = int(payload.get("priority", 16))
            else:
                value = payload
                priority = 16

            if priority < 1 or priority > 16:
                self._publish_response("write", False, f"Invalid priority {priority}. Must be 1–16.")
                return

            address = self._bacnet.get_device_address(device_id)
            if not address:
                self._publish_response("write", False, f"Device {device_id} not found")
                return

            ok = await self._bacnet.write_object(address, object_type, object_instance, value, priority)
            self._publish_response("write", ok,
                                   f"Write {'OK' if ok else 'FAILED'}: {object_type}:{object_instance} = {value} @priority {priority}")
            logger.info("CMD write: device=%d %s:%d = %s @priority %d → %s",
                        device_id, object_type, object_instance, value, priority, "OK" if ok else "FAIL")
        except Exception as exc:
            self._publish_response("write", False, str(exc))
            logger.error("CMD write error: %s", exc)

    # ── cmd/release ────────────────────────────
    async def _cmd_release(self, parts: list[str], idx: int, payload: Any) -> None:
        """Handle: <prefix>/cmd/release/<device_id>/<object_type>/<instance>
        Payload: {"priority": 8}       — release one level
                 {"priority": "all"}   — release all 1–16
        """
        try:
            device_id = int(parts[idx + 2])
            object_type = parts[idx + 3]
            object_instance = int(parts[idx + 4])

            if isinstance(payload, dict):
                priority_raw = payload.get("priority", 16)
            else:
                priority_raw = payload

            address = self._bacnet.get_device_address(device_id)
            if not address:
                self._publish_response("release", False, f"Device {device_id} not found")
                return

            if str(priority_raw).lower() == "all":
                # Release all 1–16
                results = await self._bacnet.release_all_priorities(
                    address, object_type, object_instance
                )
                success_count = sum(1 for v in results.values() if v)
                self._publish_response("release", True,
                                       f"Released {success_count}/16 priorities for {object_type}:{object_instance}",
                                       extra={"results": {str(k): v for k, v in results.items()}})
                logger.info("CMD release ALL: device=%d %s:%d → %d/16 OK",
                            device_id, object_type, object_instance, success_count)
            else:
                priority = int(priority_raw)
                if priority < 1 or priority > 16:
                    self._publish_response("release", False, f"Invalid priority {priority}. Must be 1–16.")
                    return

                ok = await self._bacnet.release_priority(address, object_type, object_instance, priority)
                self._publish_response("release", ok,
                                       f"Release {'OK' if ok else 'FAILED'}: {object_type}:{object_instance} @priority {priority}")
                logger.info("CMD release: device=%d %s:%d @priority %d → %s",
                            device_id, object_type, object_instance, priority, "OK" if ok else "FAIL")
        except Exception as exc:
            self._publish_response("release", False, str(exc))
            logger.error("CMD release error: %s", exc)

    # ── cmd/add_point ──────────────────────────
    async def _cmd_add_point(self, payload: Any) -> None:
        """Handle: <prefix>/cmd/add_point
        Payload: {"device_id": 100, "object_type": "analogValue",
                  "object_instance": 1, "poll_interval": 10, "label": "Room Temp"}
        """
        try:
            if not isinstance(payload, dict):
                self._publish_response("add_point", False, "Payload must be a JSON object")
                return

            mapping = PointMapping(
                device_id=int(payload["device_id"]),
                object_type=payload["object_type"],
                object_instance=int(payload["object_instance"]),
                poll_interval=int(payload.get("poll_interval", self._cm.config.bacnet.default_poll_interval)),
                label=payload.get("label", ""),
                mqtt_topic=payload.get("mqtt_topic", ""),
                enabled=payload.get("enabled", True),
            )

            created = self._cm.add_mapping(mapping)
            self._publish_response("add_point", True,
                                   f"Added point {mapping.object_type}:{mapping.object_instance} on device {mapping.device_id}",
                                   extra={"mapping_id": created.id})
            logger.info("CMD add_point: %s:%d on device %d (id=%s)",
                        mapping.object_type, mapping.object_instance, mapping.device_id, created.id)
        except Exception as exc:
            self._publish_response("add_point", False, str(exc))
            logger.error("CMD add_point error: %s", exc)

    # ── cmd/remove_point ───────────────────────
    async def _cmd_remove_point(self, payload: Any) -> None:
        """Handle: <prefix>/cmd/remove_point
        Payload: {"device_id": 100, "object_type": "analogValue", "object_instance": 1}
              or {"mapping_id": "abc12345"}
        """
        try:
            if not isinstance(payload, dict):
                self._publish_response("remove_point", False, "Payload must be a JSON object")
                return

            # Try by mapping_id first
            mapping_id = payload.get("mapping_id")
            if mapping_id:
                removed = self._cm.remove_mapping(mapping_id)
                self._publish_response("remove_point", removed,
                                       f"{'Removed' if removed else 'Not found'} mapping {mapping_id}")
                return

            # Otherwise match by device_id + object_type + object_instance
            device_id = int(payload["device_id"])
            object_type = payload["object_type"]
            object_instance = int(payload["object_instance"])

            removed_any = False
            for m in list(self._cm.mappings):
                if (m.device_id == device_id
                        and m.object_type == object_type
                        and m.object_instance == object_instance):
                    self._cm.remove_mapping(m.id)
                    removed_any = True

            self._publish_response("remove_point", removed_any,
                                   f"{'Removed' if removed_any else 'Not found'} {object_type}:{object_instance} on device {device_id}")
            logger.info("CMD remove_point: %s:%d on device %d → %s",
                        object_type, object_instance, device_id, "removed" if removed_any else "not found")
        except Exception as exc:
            self._publish_response("remove_point", False, str(exc))
            logger.error("CMD remove_point error: %s", exc)

    # ── cmd/list_points ────────────────────────
    async def _cmd_list_points(self) -> None:
        """Handle: <prefix>/cmd/list_points — publishes current mapping list."""
        try:
            points = []
            for m in self._cm.mappings:
                points.append({
                    "mapping_id": m.id,
                    "device_id": m.device_id,
                    "object_type": m.object_type,
                    "object_instance": m.object_instance,
                    "label": m.label,
                    "poll_interval": m.poll_interval,
                    "enabled": m.enabled,
                    "mqtt_topic": m.mqtt_topic,
                    "last_value": m.last_value,
                    "priority_array": m.priority_array,
                })

            self._publish_response("list_points", True,
                                   f"{len(points)} points configured",
                                   extra={"points": points})
            logger.info("CMD list_points: %d points", len(points))
        except Exception as exc:
            self._publish_response("list_points", False, str(exc))
            logger.error("CMD list_points error: %s", exc)
