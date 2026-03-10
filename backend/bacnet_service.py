"""BACnet service — wraps BAC0 for device discovery, reading, writing, and priority array."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.models import BacnetConfig, BacnetDevice, BacnetObject

# Object types we can meaningfully read present_value from via BACnet.
# Everything NOT in this set (notificationClass, file, program, calendar,
# schedule, trendLog, eventEnrollment, loop, command, averaging, etc.)
# is silently skipped during object-list scan and cannot be mapped.
POLLABLE_TYPES: frozenset[str] = frozenset({
    "analogInput", "analogOutput", "analogValue",
    "binaryInput", "binaryOutput", "binaryValue",
    "multiStateInput", "multiStateOutput", "multiStateValue",
    "device",
    # alternate hyphenated forms sometimes returned by BAC0
    "analog-input", "analog-output", "analog-value",
    "binary-input", "binary-output", "binary-value",
    "multi-state-input", "multi-state-output", "multi-state-value",
})

logger = logging.getLogger(__name__)

# Map hyphenated BACnet type names to camelCase (what BAC0 expects)
_TYPE_NORMALIZE = {
    'analog-input': 'analogInput',
    'analog-output': 'analogOutput',
    'analog-value': 'analogValue',
    'binary-input': 'binaryInput',
    'binary-output': 'binaryOutput',
    'binary-value': 'binaryValue',
    'multi-state-input': 'multiStateInput',
    'multi-state-output': 'multiStateOutput',
    'multi-state-value': 'multiStateValue',
}


def _normalize_type(object_type: str) -> str:
    """Convert hyphenated BACnet type to camelCase for BAC0."""
    return _TYPE_NORMALIZE.get(object_type, object_type)


class BacnetService:
    """Thin async wrapper around BAC0 for BACnet/IP communication."""

    def __init__(self, config: BacnetConfig):
        self._config = config
        self._network: Any = None
        self._connected = False
        self._devices: dict[int, BacnetDevice] = {}
        self._device_names: dict[int, str] = {}  # cached device names
        self._name_task: Any = None  # background name reading task
        self._read_lock = asyncio.Lock()  # prevent concurrent BACnet reads
        # True COV: track active BAC0 COVSubscription tasks
        # key: "address:type:instance" → asyncio.Task
        self._cov_tasks: dict[str, Any] = {}
        self._cov_callbacks: dict[str, Any] = {}  # key → async callback(value)

    # ── lifecycle ──────────────────────────────
    async def start(self) -> None:
        """Initialise BAC0 network connection."""
        try:
            import BAC0

            # Kill any stale process holding the BACnet UDP port
            self._kill_stale_bacnet_port(self._config.port)

            ip_str = self._config.ip
            if ip_str and ip_str != "0.0.0.0":
                # BAC0 requires IP/mask format, e.g. '10.25.7.21/24'
                mask = self._config.mask
                if mask and '/' not in ip_str:
                    ip_str = f"{ip_str}/{mask}"
            else:
                ip_str = None

            # Silence BAC0 debug spam BEFORE creating Lite instance
            import logging as _logging
            for _n in ('BAC0_Root', 'BAC0', 'bacpypes', 'BACpypes3'):
                _logging.getLogger(_n).setLevel(_logging.WARNING)

            self._network = BAC0.lite(
                ip=ip_str,
                port=self._config.port,
            )

            # Wait for BACpypes3 transport tasks to complete
            await asyncio.sleep(2)

            # BAC0 Lite resets its own loggers during __init__, so suppress AGAIN after init
            for _n in ('BAC0_Root', 'BAC0', 'bacpypes', 'BACpypes3',
                       'BAC0_Root.BAC0.scripts', 'BAC0_Root.BAC0.core', 'BAC0_Root.BAC0.tasks'):
                _l = _logging.getLogger(_n)
                _l.setLevel(_logging.WARNING)
                _l.propagate = False

            # Restore broadcast socket patch (vital for gateway operation)
            try:
                app = self._network.this_application.app
                for oid, ll in app.link_layers.items():
                    server = ll.server
                    if not hasattr(server, '_local_transport_ready'):
                        continue
                    if server.broadcast_protocol and server.local_protocol:
                        # Key fix: broadcast responses should be tagged as
                        # arriving at our local address, not as broadcasts
                        old_dest = server.broadcast_protocol.destination
                        server.broadcast_protocol.destination = server.local_protocol.destination
                        logger.info("✅ Broadcast protocol destination changed: %s → %s",
                                    old_dest, server.broadcast_protocol.destination)
            except Exception as e:
                logger.warning("Failed to patch broadcast destination: %s", e)

            self._connected = True
            logger.info("BACnet service started on %s:%s", ip_str or 'auto', self._config.port)
        except Exception as exc:
            logger.error("Failed to start BACnet service: %s", exc)
            self._connected = False
            raise

    @staticmethod
    def _kill_stale_bacnet_port(port: int) -> None:
        """Force-release any stale process holding the BACnet UDP port."""
        import subprocess
        try:
            result = subprocess.run(
                ["fuser", "-k", "-n", "udp", str(port)],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                logger.warning("Killed stale process(es) on UDP port %d: %s", port, result.stdout.strip())
                import time
                time.sleep(0.5)
        except FileNotFoundError:
            # fuser not available, try ss
            try:
                result = subprocess.run(
                    ["ss", "-ulnp", "sport", f"= :{port}"],
                    capture_output=True, text=True, timeout=5,
                )
                if 'pid=' in result.stdout:
                    import re
                    pids = re.findall(r'pid=(\d+)', result.stdout)
                    for pid in pids:
                        try:
                            import os, signal
                            os.kill(int(pid), signal.SIGKILL)
                            logger.warning("Killed stale PID %s on UDP port %d", pid, port)
                        except OSError:
                            pass
                    import time
                    time.sleep(0.5)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Port cleanup check: %s", exc)

    async def stop(self) -> None:
        """Shut down BAC0 network and release UDP socket."""
        if self._network is not None:
            try:
                self._network.disconnect()
            except Exception:
                pass
            # Force cleanup BACpypes3 internal sockets
            try:
                import asyncio
                for task in asyncio.all_tasks():
                    if 'BACpypes' in str(task) or 'BAC0' in str(task):
                        task.cancel()
            except Exception:
                pass
            self._network = None
        self._connected = False
        logger.info("BACnet service stopped.")

    @property
    def connected(self) -> bool:
        return self._connected

    # ── discovery ──────────────────────────────
    async def discover_devices(
        self,
        timeout: int = 10,
        scan_mode: str = "full",
        low_id: int | None = None,
        high_id: int | None = None,
        device_id: int | None = None,
    ) -> list[BacnetDevice]:
        """Broadcast Who-Is and return discovered devices.

        scan_mode:
            'full'     — broadcast WHO-IS to all devices
            'range'    — WHO-IS with device ID range [low_id, high_id]
            'specific' — WHO-IS targeting a single device_id
        """
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        devices: list[BacnetDevice] = []
        try:
            existing = self._network.discoveredDevices or {}
            need_scan = True

            # For range/specific: if we already have cached devices, skip scan
            if scan_mode in ("range", "specific") and len(existing) > 0:
                logger.info("Using %d cached devices for %s filter", len(existing), scan_mode)
                need_scan = False

            if need_scan:
                logger.info("Running BAC0 discover (mode=%s, timeout=%ds)", scan_mode, timeout)
                try:
                    self._network.discover(global_broadcast=True)
                except TypeError:
                    self._network.discover()

            # Wait for responses
            await asyncio.sleep(timeout)

            # Get discovered devices from discoveredDevices dict
            discovered_dict = self._network.discoveredDevices or {}
            total = len(discovered_dict)
            logger.info("Found %d raw devices (mode=%s)", total, scan_mode)

            # Filter by range/specific if applicable (in case broadcast was used)
            filtered_items = []
            for key, info in list(discovered_dict.items()):
                obj_inst = info.get("object_instance", (None, 0))
                if isinstance(obj_inst, (list, tuple)) and len(obj_inst) >= 2:
                    instance = int(obj_inst[1])
                else:
                    instance = int(obj_inst) if obj_inst else 0

                if scan_mode == "specific" and device_id is not None:
                    if instance != device_id:
                        continue
                elif scan_mode == "range" and low_id is not None and high_id is not None:
                    if instance < low_id or instance > high_id:
                        continue

                filtered_items.append((key, info, instance))

            logger.info("Filtered to %d devices (from %d raw)", len(filtered_items), total)

            # Build device list with cached names (read on-demand via /api/bacnet/devices/{id}/name)
            for key, info, instance in filtered_items:
                address = str(info.get("address", ""))
                vendor_name = str(info.get("vendor_name", ""))
                vendor_id = info.get("vendor_id", None)

                # Parse network ID from address (e.g. "1600:3" → "1600")
                network_id = ""
                if ":" in address and not address.startswith("["):
                    parts = address.split(":")
                    if len(parts) == 2 and parts[0].isdigit():
                        network_id = parts[0]

                name = self._device_names.get(instance, f"Device {instance}")
                device = BacnetDevice(
                    device_id=instance,
                    device_name=name,
                    address=address,
                    vendor_name=vendor_name if vendor_name != "unknown" else f"Vendor {vendor_id}",
                    network_id=network_id,
                )
                devices.append(device)
                self._devices[instance] = device

            logger.info("Discovered %d BACnet devices (mode=%s).", len(devices), scan_mode)

        except Exception as exc:
            logger.error("Discovery failed: %s", exc)

        return devices

    async def read_device_name(self, device_id: int) -> str:
        """Read a single device's objectName on-demand. Caches the result.

        Fallback chain:
          1. _device_names cache (fastest)
          2. _devices dict (populated by discover_devices scan)
          3. _network.discoveredDevices (BAC0 internal – populated by I-Am responses)
        """
        if device_id in self._device_names:
            return self._device_names[device_id]

        if not self._connected or self._network is None:
            return f"Device {device_id}"

        # Resolve address: try _devices first, then BAC0's internal dict
        address: str | None = None
        device = self._devices.get(device_id)
        if device:
            address = device.address
        else:
            # Fallback: search BAC0's discoveredDevices (keyed by address object)
            try:
                for addr_key, dev_obj in self._network.discoveredDevices.items():
                    # BAC0 stores devices as {address: device} where device has .device_id
                    dev_inst = getattr(dev_obj, "deviceInstanceRangeHighLimit", None)
                    if dev_inst is None:
                        # Try alternate attribute names
                        dev_inst = getattr(dev_obj, "instance", None)
                    if dev_inst == device_id:
                        address = str(addr_key)
                        break
            except Exception:
                pass

        if not address:
            return f"Device {device_id}"

        try:
            name = await self._network.read(
                f"{address} device {device_id} objectName"
            )
            if name:
                name_str = str(name).strip()
                self._device_names[device_id] = name_str
                if device:
                    device.device_name = name_str
                return name_str
        except Exception as exc:
            logger.debug("[read_device_name] device %d addr %s: %s", device_id, address, exc)
        return f"Device {device_id}"

    async def read_device_names_batch(self, device_ids: list[int]) -> dict[int, str]:
        """Read names for a batch of devices sequentially. Keeps BAC0 queue clear."""
        results = {}
        for did in device_ids:
            name = await self.read_device_name(did)
            results[did] = name
        return results

    # ── read ───────────────────────────────────
    async def read_object(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        property_name: str = "presentValue",
    ) -> Any:
        """Read a single BACnet object property."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        try:
            ot = _normalize_type(object_type)
            request_str = f"{address} {ot} {object_instance} {property_name}"
            value = await self._network.read(request_str)
            return value
        except Exception as exc:
            logger.error("Read failed for %s %s %d: %s", address, object_type, object_instance, exc)
            return None

    async def read_priority_array(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[str, Any]:
        """Read the full 16-level priorityArray of a commandable BACnet object."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        result: dict[str, Any] = {}
        try:
            ot = _normalize_type(object_type)
            # First try BAC0 native read_priority_array (async)
            try:
                raw = await self._network.read_priority_array(
                    f"{address} {ot} {object_instance}"
                )
                if raw and hasattr(raw, '__iter__'):
                    for i, val in enumerate(raw, start=1):
                        if i > 16:
                            break
                        result[str(i)] = self._normalise_priority_value(val)
                    return result
            except Exception:
                pass

            # Fallback: read priorityArray property directly
            request_str = f"{address} {ot} {object_instance} priorityArray"
            raw = await self._network.read(request_str)

            if raw is None:
                return result

            if isinstance(raw, (list, tuple)):
                for i, val in enumerate(raw, start=1):
                    result[str(i)] = self._normalise_priority_value(val)
            elif hasattr(raw, '__iter__'):
                for i, val in enumerate(raw, start=1):
                    if i > 16:
                        break
                    result[str(i)] = self._normalise_priority_value(val)
            else:
                # Read each slot individually
                for i in range(1, 17):
                    try:
                        slot_req = f"{address} {ot} {object_instance} priorityArray {i}"
                        val = await self._network.read(slot_req)
                        result[str(i)] = self._normalise_priority_value(val)
                    except Exception:
                        result[str(i)] = None

        except Exception as exc:
            logger.debug("priorityArray read failed for %s %s %d: %s (may not be commandable)",
                         address, object_type, object_instance, exc)

        return result

    @staticmethod
    def _normalise_priority_value(val: Any) -> Any:
        """Convert BACpypes Null / priority-value wrapper to Python None or float/int."""
        if val is None:
            return None

        # Handle PriorityValue objects (bacpypes3)
        # These have a '_choice' attribute indicating which field is active
        if hasattr(val, '_choice'):
            choice = getattr(val, '_choice', 'null')
            if choice == 'null':
                return None
            # Get the value from the active choice field
            inner = getattr(val, choice, None)
            if inner is None:
                return None
            # Convert BACpypes enums/types to Python primitives
            inner_str = str(inner).strip()
            if inner_str.lower() in ('null', 'none', ''):
                return None
            # Try numeric conversion
            try:
                return float(inner) if '.' in inner_str else int(inner)
            except (ValueError, TypeError):
                return inner_str

        val_str = str(val).strip().lower()
        if val_str in ("null", "none", "(null)", ""):
            return None
        # Try numeric parse as fallback
        try:
            return float(val) if '.' in val_str else int(val)
        except (ValueError, TypeError):
            return val_str if val_str not in ("null", "none") else None

    # ── Alarm / Event State ────────────────────
    async def read_event_state(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> str | None:
        """Read eventState property of a BACnet object.

        Returns: 'normal', 'fault', 'offnormal', 'high-limit', 'low-limit', or None.
        """
        if not self._connected or self._network is None:
            return None
        ot = _normalize_type(object_type)
        try:
            result = await self._network.read(
                f"{address} {ot} {object_instance} eventState",
            )
            if result is None:
                return None
            return str(result).strip().lower()
        except Exception:
            return None  # Object doesn't support eventState

    # ── COV (Change of Value) Subscription ──────────────────────────────────
    # True BACnet COV using BAC0.scripts.Lite.COVSubscription.
    # The device sends UnconfirmedCOVNotification whenever presentValue changes.
    # A background asyncio.Task per subscription handles incoming notifications.
    #
    # Lifetime is set to COV_LIFETIME seconds; the task auto-renews.
    # If the device doesn't support COV (no response within 10s), we log a
    # warning and the caller must fall back to polling.

    COV_LIFETIME = 300      # seconds; re-subscribe before expiry
    COV_RENEW_EARLY = 30    # renew this many seconds before expiry
    MAX_COV_FAILURES = 3    # consecutive failures before falling back to poll

    async def subscribe_cov(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        callback,            # async callback(value)
        lifetime: int = 0,   # 0 → use COV_LIFETIME
        on_fallback=None,    # async callback() called when COV gives up → caller should switch to poll
    ) -> bool:
        """Subscribe to True BACnet COV notifications for a single object.

        If the device rejects or doesn't respond to COV requests MAX_COV_FAILURES
        times in a row, `on_fallback()` is called so the caller can switch the
        mapping back to read_mode='poll'.
        """
        if not self._connected or self._network is None:
            return False

        ot = _normalize_type(object_type)
        key = f"{address}:{ot}:{object_instance}"

        # Cancel existing subscription for this key if any
        await self.unsubscribe_cov(address, object_type, object_instance)

        self._cov_callbacks[key] = callback
        effective_lifetime = lifetime if lifetime > 0 else self.COV_LIFETIME

        task = asyncio.create_task(
            self._cov_subscription_loop(key, address, ot, object_instance, callback, effective_lifetime, on_fallback),
            name=f"cov-{key}",
        )
        self._cov_tasks[key] = task
        logger.info("COV subscription started: %s (lifetime=%ds)", key, effective_lifetime)
        return True

    async def _cov_subscription_loop(
        self,
        key: str,
        address: str,
        object_type: str,
        object_instance: int,
        callback,
        lifetime: int,
        on_fallback=None,   # called when giving up after MAX_COV_FAILURES
    ) -> None:
        """Long-running COV subscription task.

        Loops until unsubscribed or MAX_COV_FAILURES consecutive failures.
        On repeated failure, calls on_fallback() so caller can switch to poll.
        """
        try:
            from BAC0.scripts.Lite import COVSubscription
        except ImportError:
            logger.error("COVSubscription not available in this BAC0 version")
            return

        obj_id = (object_type, object_instance)
        consecutive_failures = 0

        while key in self._cov_callbacks:
            try:
                sub = COVSubscription(
                    address=address,
                    objectID=obj_id,
                    lifetime=lifetime,
                    confirmed=False,     # Unconfirmed COV — lighter on network
                    callback=None,
                    BAC0App=self._network,
                )

                app = self._network.this_application.app
                renew_at = asyncio.get_event_loop().time() + lifetime - self.COV_RENEW_EARLY

                async with app.change_of_value(
                    sub.address,
                    sub.obj_identifier,
                    sub.process_identifier,
                    sub.confirmed,
                    sub.lifetime,
                ) as scm:
                    logger.info("COV active: %s", key)
                    consecutive_failures = 0   # subscription accepted → reset failure count

                    while key in self._cov_callbacks:
                        time_left = renew_at - asyncio.get_event_loop().time()
                        if time_left <= 0:
                            logger.debug("COV renewing: %s", key)
                            break  # exit context manager → re-subscribe

                        try:
                            incoming = asyncio.ensure_future(scm.get_value())
                            done, pending = await asyncio.wait(
                                [incoming],
                                timeout=min(time_left, 10.0),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for t in pending:
                                t.cancel()

                            if incoming in done:
                                prop_id, prop_val = incoming.result()
                                pid_str = str(prop_id).lower()
                                if "present" in pid_str or pid_str == "presentvalue":
                                    value = self._extract_cov_value(prop_val)
                                    if callback is not None and key in self._cov_callbacks:
                                        if asyncio.iscoroutinefunction(callback):
                                            asyncio.create_task(callback(value))
                                        else:
                                            callback(value)
                        except asyncio.CancelledError:
                            return
                        except Exception as exc:
                            logger.debug("COV get_value error (%s): %s", key, exc)
                            await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info("COV subscription cancelled: %s", key)
                return
            except Exception as exc:
                consecutive_failures += 1
                logger.warning(
                    "COV subscription error (%s) [%d/%d]: %s",
                    key, consecutive_failures, self.MAX_COV_FAILURES, exc
                )

                if consecutive_failures >= self.MAX_COV_FAILURES:
                    logger.error(
                        "COV giving up after %d failures for %s — falling back to poll",
                        consecutive_failures, key,
                    )
                    # Clean up subscription entry
                    self._cov_callbacks.pop(key, None)
                    self._cov_tasks.pop(key, None)
                    # Notify caller to switch mapping back to poll
                    if on_fallback is not None:
                        try:
                            if asyncio.iscoroutinefunction(on_fallback):
                                asyncio.create_task(on_fallback())
                            else:
                                on_fallback()
                        except Exception as fe:
                            logger.debug("COV fallback callback error: %s", fe)
                    return

                # Exponential-ish back-off: 30s, 60s, 90s...
                wait = 30 * consecutive_failures
                logger.info("COV retrying %s in %ds...", key, wait)
                await asyncio.sleep(wait)

        logger.info("COV subscription loop ended: %s", key)

    def _extract_cov_value(self, prop_val: Any) -> Any:
        """Extract a Python primitive from a BACpypes3 COV property value."""
        try:
            # Enumerated types (active/inactive, etc.)
            if hasattr(prop_val, "value"):
                v = prop_val.value
                if isinstance(v, (int, float, str, bool)):
                    return v
            # Direct numeric / string
            if isinstance(prop_val, (int, float, str, bool)):
                return prop_val
            # Fallback: stringify
            return str(prop_val)
        except Exception:
            return str(prop_val)

    async def unsubscribe_cov(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> bool:
        """Cancel a COV subscription and stop the background task."""
        ot = _normalize_type(object_type)
        key = f"{address}:{ot}:{object_instance}"
        self._cov_callbacks.pop(key, None)
        task = self._cov_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("COV unsubscribed: %s", key)
        return True

    def unsubscribe_all_cov(self):
        """Cancel all active COV subscriptions."""
        keys = list(self._cov_tasks.keys())
        self._cov_callbacks.clear()
        for task in self._cov_tasks.values():
            if not task.done():
                task.cancel()
        self._cov_tasks.clear()
        logger.info("Unsubscribed all COV (%d)", len(keys))

    async def read_object_properties(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[str, Any]:
        """Read extended BACnet properties: units, stateText, description, etc."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        props: dict[str, Any] = {}
        prop_names = [
            ("units", "units"),
            ("description", "description"),
            ("stateText", "state_text"),
            ("activeText", "active_text"),
            ("inactiveText", "inactive_text"),
        ]

        ot = _normalize_type(object_type)
        for bacnet_prop, key in prop_names:
            try:
                val = await self._network.read(
                    f"{address} {ot} {object_instance} {bacnet_prop}"
                )
                if val is not None:
                    # Convert BACpypes enum to string
                    val_str = str(val)
                    if val_str.lower() not in ("none", "null", ""):
                        if bacnet_prop == "stateText" and hasattr(val, '__iter__'):
                            props[key] = [str(s) for s in val]
                        else:
                            props[key] = val_str
            except Exception:
                pass  # Property not supported by this object type

        return props

    async def read_object_list(self, address: str, device_id: int) -> list[BacnetObject]:
        """Read the object list of a device.
        Falls back to index-based reads if full list fails.
        Uses direct await (no asyncio.wait_for) to avoid corrupting BAC0 internals.
        """
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        is_mstp = ":" in address and not address.startswith("[") and "." not in address

        objects: list[BacnetObject] = []
        try:
            logger.info("Reading object list for device %d at %s (MSTP=%s)",
                        device_id, address, is_mstp)

            # Try full objectList read (BAC0 handles its own internal timeout)
            obj_list = None
            try:
                obj_list = await self._network.read(
                    f"{address} device {device_id} objectList"
                )
            except Exception as e:
                logger.warning("Full objectList read failed for %d: %s. Trying index-based...", device_id, e)

            # Fallback: read objectList by index
            if not obj_list:
                obj_list = []
                for idx in range(1, 200):
                    try:
                        item = await self._network.read(
                            f"{address} device {device_id} objectList {idx}"
                        )
                        if item is None:
                            break
                        obj_list.append(item)
                    except Exception:
                        break
                if obj_list:
                    logger.info("Index-based read got %d objects for device %d", len(obj_list), device_id)

            if obj_list:
                raw_objects = []
                for item in obj_list:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        raw_objects.append((str(item[0]), item[1]))
                    else:
                        try:
                            otype = getattr(item, 'objectType', None) or str(item[0]) if hasattr(item, '__getitem__') else str(item)
                            oinst = getattr(item, 'objectInstance', None) or (item[1] if hasattr(item, '__getitem__') else 0)
                            raw_objects.append((str(otype), int(oinst)))
                        except Exception as e:
                            logger.debug("Skip unparseable object: %s (%s)", item, e)

                # Filter to pollable types only — skip notificationClass, file, program, etc.
                filtered_raw = [(ot, oi) for ot, oi in raw_objects if ot.lower() in {t.lower() for t in POLLABLE_TYPES}]
                skipped = len(raw_objects) - len(filtered_raw)
                if skipped:
                    logger.info("Device %d: skipped %d non-pollable objects (NOT/FIL/PRG/…)", device_id, skipped)
                raw_objects = filtered_raw

                logger.info("Device %d: %d pollable objects found, reading names...", device_id, len(raw_objects))


                # Read names sequentially (avoid BAC0 queue saturation)
                for ot, oi in raw_objects:
                    name = ""
                    try:
                        n = await self._network.read(f"{address} {ot} {oi} objectName")
                        name = str(n) if n else ""
                    except Exception:
                        pass
                    objects.append(BacnetObject(
                        object_type=ot,
                        object_instance=oi,
                        object_name=name,
                    ))

            logger.info("Read %d objects from device %d", len(objects), device_id)
        except Exception as exc:
            logger.error("Object list read failed for device %d: %s", device_id, exc)

        return objects

    # ── write ──────────────────────────────────
    async def write_object(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        value: Any,
        priority: int = 16,
    ) -> tuple[bool, str]:
        """Write a value to a BACnet object at the given priority (1–16).
        Returns (success, error_message).
        
        NOTE: We call BAC0's internal _write() directly (with await) instead of
        the public write() method, because write() is fire-and-forget (DoOnce task)
        and always returns before the BACnet write actually completes.
        """
        if not self._connected or self._network is None:
            return False, "BACnet service not started"

        try:
            ot = _normalize_type(object_type)

            # Normalize value: BAC0 _write() expects numeric for binary (1/0), not 'active'/'inactive'
            write_val = value
            ot_lower = ot.lower()
            if ot_lower in ("binaryoutput", "binaryvalue", "bo", "bv"):
                if isinstance(write_val, str):
                    if write_val.lower() in ("active", "on", "true", "1", "yes"):
                        write_val = 1
                    else:
                        write_val = 0
                elif isinstance(write_val, bool):
                    write_val = int(write_val)
            elif isinstance(write_val, float) and write_val == int(write_val):
                write_val = int(write_val)  # send 22 not 22.0 for cleaner request

            request_str = f"{address} {ot} {object_instance} presentValue {write_val} - {priority}"
            logger.info("Write request (awaited): %s", request_str)
            # Use _write (async) directly — write() is fire-and-forget!
            response = await self._network._write(request_str)
            logger.info("Write response: %s %s %d = %s @P%d -> %s",
                        address, object_type, object_instance, write_val, priority, response)
            return True, ""
        except Exception as exc:
            err_msg = str(exc)
            logger.error("Write failed for %s %s %d = %s @P%d: %s",
                         address, object_type, object_instance, value, priority, err_msg)
            # Provide user-friendly error messages
            if 'writeAccessDenied' in err_msg or 'readOnly' in err_msg.lower():
                return False, "Write Access Denied — point is read-only"
            if 'unknownProperty' in err_msg:
                return False, "Object does not support writing"
            if 'invalidDataType' in err_msg or 'Invalid value for property' in err_msg:
                return False, f"Invalid value '{value}' for {object_type}"
            if 'NoResponseFromController' in err_msg or 'no response' in err_msg.lower():
                return False, "No response from device — check network connection"
            if 'Abort' in err_msg:
                return False, f"Device aborted write: {err_msg}"
            return False, err_msg


    # ── release ────────────────────────────────
    async def release_priority(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        priority: int,
    ) -> bool:
        """Release (write null to) a single priority level."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        try:
            ot = _normalize_type(object_type)
            request_str = f"{address} {ot} {object_instance} presentValue null - {priority}"
            logger.info("Release request (awaited): %s", request_str)
            # Use _write (async) directly — write() is fire-and-forget!
            await self._network._write(request_str)
            logger.info("Release OK: %s %s %d @priority %d",
                        address, object_type, object_instance, priority)
            return True
        except Exception as exc:
            logger.error("Release failed @priority %d: %s", priority, exc)
            return False

    async def release_all_priorities(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[int, bool]:
        """Release all 16 priority levels. Returns {priority: success}."""
        results: dict[int, bool] = {}
        for pri in range(1, 17):
            ok = await self.release_priority(address, object_type, object_instance, pri)
            results[pri] = ok
        return results

    # ── helpers ────────────────────────────────
    def get_device(self, device_id: int) -> BacnetDevice | None:
        return self._devices.get(device_id)

    def get_device_address(self, device_id: int) -> str | None:
        dev = self._devices.get(device_id)
        return dev.address if dev else None

    @property
    def discovered_devices(self) -> list[BacnetDevice]:
        return list(self._devices.values())
