"""FastAPI application — REST API + WebSocket + static file serving."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.bacnet_service import BacnetService
from backend.config_manager import ConfigManager
from backend.gateway_engine import GatewayEngine
from backend.history_store import HistoryStore
from backend.scheduler_service import SchedulerService
from backend.models import (
    AlarmConfig,
    BacnetConfig,
    ChartConfig,
    DiscoveryRequest,
    GroupConfig,
    MqttConfig,
    MqttTestRequest,
    PointMapping,
    ReleaseRequest,
    ScheduleEntry,
    StatusResponse,
    UserConfig,
    WebhookConfig,
    WriteRequest,
)
from backend.auth_service import (
    hash_password, verify_password, create_token,
    require_auth, require_operator, require_admin,
)
from backend.mqtt_service import MqttService
from backend.websocket_manager import WebSocketManager
from backend.health_monitor import get_system_health, check_ram_for_new_points
from backend.device_registry import DeviceRegistry
from backend.webhook_service import WebhookService

# ── Logging setup ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Shared instances ───────────────────────────
config_manager = ConfigManager()
ws_manager = WebSocketManager()

# These are initialised in lifespan
bacnet_service: BacnetService | None = None
mqtt_service: MqttService | None = None
gateway_engine: GatewayEngine | None = None
history_store: HistoryStore | None = None
scheduler_service: SchedulerService | None = None
device_registry: DeviceRegistry | None = None
webhook_service: WebhookService | None = None


# ── Lifespan (startup / shutdown) ──────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global bacnet_service, mqtt_service, gateway_engine, history_store, scheduler_service, device_registry, webhook_service

    # Load config
    cfg = config_manager.load()

    # Initialise history store
    history_store = HistoryStore()
    history_store.init()

    # Initialise device registry (persistent)
    device_registry = DeviceRegistry(path="data/device_registry.json")
    logger.info(f"DeviceRegistry: {device_registry.total()} devices loaded from disk")

    # Initialise services
    bacnet_service = BacnetService(cfg.bacnet)
    mqtt_service = MqttService(cfg.mqtt)
    webhook_service = WebhookService(config_manager)
    gateway_engine = GatewayEngine(
        config_manager, bacnet_service, mqtt_service, ws_manager,
        history_store=history_store,
        webhook_service=webhook_service,
    )

    # Initialize AnomalyEngine and wire to gateway
    try:
        from backend.anomaly_engine import AnomalyEngine
        _anomaly_engine = AnomalyEngine(config_manager, history_store, mqtt_service)
        gateway_engine._anomaly = _anomaly_engine
        logger.info("AnomalyEngine initialized and wired to GatewayEngine.")
    except Exception as ae_err:
        logger.warning("AnomalyEngine init failed (non-critical): %s", ae_err)

    # Start MQTT (fire and forget — broker may not be reachable yet)
    try:
        mqtt_service.start()
    except Exception as exc:
        logger.warning("MQTT start deferred: %s", exc)

    # Start history cleanup loop
    asyncio.create_task(history_store.start_cleanup_loop(interval_minutes=60))

    # Auto-start gateway (BACnet + polling) in background
    asyncio.create_task(_auto_start_gateway())

    # Start scheduler
    scheduler_service = SchedulerService(config_manager, bacnet_service, history_store)
    scheduler_service.start()

    logger.info("Gateway application ready.")
    yield

    # Shutdown
    if gateway_engine:
        await gateway_engine.stop()
    if scheduler_service:
        scheduler_service.stop()
    if mqtt_service:
        mqtt_service.stop()
    if bacnet_service:
        await bacnet_service.stop()
    if history_store:
        history_store.close()
    logger.info("Gateway application shut down.")


async def _auto_start_gateway():
    """Background task: auto-connect BACnet and start gateway on boot."""
    global bacnet_service, gateway_engine

    # Wait for app to be fully ready
    await asyncio.sleep(3)

    if not bacnet_service or not gateway_engine:
        logger.warning("[Auto-Start] Services not initialised, skipping.")
        return

    # Check if there are mappings to run
    mappings = config_manager.mappings
    if not mappings:
        logger.info("[Auto-Start] No mappings configured — skipping gateway start.")
        return

    logger.info("[Auto-Start] Found %d mappings — starting gateway automatically…", len(mappings))

    # Step 1: Connect BACnet
    try:
        if not bacnet_service.connected:
            logger.info("[Auto-Start] Connecting BACnet (IP=%s, mask=%s, port=%d)…",
                        config_manager.config.bacnet.ip,
                        config_manager.config.bacnet.mask,
                        config_manager.config.bacnet.port)
            await bacnet_service.start()
            logger.info("[Auto-Start] ✅ BACnet connected.")
        else:
            logger.info("[Auto-Start] BACnet already connected.")
    except Exception as exc:
        logger.error("[Auto-Start] ❌ BACnet connection failed: %s — will retry in 30s", exc)
        await asyncio.sleep(30)
        try:
            await bacnet_service.start()
            logger.info("[Auto-Start] ✅ BACnet connected on retry.")
        except Exception as exc2:
            logger.error("[Auto-Start] ❌ BACnet retry failed: %s — gateway NOT started. "
                         "Use the UI to connect manually.", exc2)
            return

    # Step 2: Discover devices referenced in mappings (WHO-IS)
    device_ids = list(set(m.device_id for m in mappings))
    logger.info("[Auto-Start] Discovering %d device(s): %s", len(device_ids), device_ids)
    try:
        # Use full broadcast — BAC0 specific WHO-IS often times out on first boot
        discovered = await bacnet_service.discover_devices(scan_mode="full", timeout=10)
        await asyncio.sleep(2)
        found = len(bacnet_service._devices)
        logger.info("[Auto-Start] Discovery round 1: found %d device(s).", found)
        if discovered and gateway_engine:
            gateway_engine.register_discovered_devices(discovered)

        # Retry if no devices found
        if found == 0:
            logger.info("[Auto-Start] Retrying discovery with longer timeout…")
            await asyncio.sleep(3)
            discovered = await bacnet_service.discover_devices(scan_mode="full", timeout=15)
            await asyncio.sleep(3)
            found = len(bacnet_service._devices)
            logger.info("[Auto-Start] Discovery round 2: found %d device(s).", found)
            if discovered and gateway_engine:
                gateway_engine.register_discovered_devices(discovered)
    except Exception as exc:
        logger.warning("[Auto-Start] Device discovery error: %s — proceeding anyway", exc)

    # Step 3: Start gateway engine (polling + MQTT commands)
    try:
        await gateway_engine.start()
        logger.info("[Auto-Start] ✅ Gateway started — polling %d mappings.", len(mappings))
    except Exception as exc:
        logger.error("[Auto-Start] ❌ Gateway start failed: %s", exc)


# ── FastAPI app ────────────────────────────────
_base = Path(__file__).resolve().parent.parent
FRONTEND_V2_DIR = _base / "frontend_v2" / "dist"
FRONTEND_LEGACY_DIR = _base / "frontend"

# Use React V2 if built, else fall back to legacy vanilla frontend
FRONTEND_DIR = FRONTEND_V2_DIR if (FRONTEND_V2_DIR / "index.html").exists() else FRONTEND_LEGACY_DIR

app = FastAPI(
    title="BACnet-MQTT Gateway",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress static assets & API responses — reduces 1.8MB JS to ~510KB
app.add_middleware(GZipMiddleware, minimum_size=500)


# ── Static frontend serving ───────────────────
@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")

# Mount static assets AFTER the root route
if FRONTEND_DIR == FRONTEND_V2_DIR:
    # React build: mount the assets dir
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
else:
    # Legacy vanilla: mount css and js
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


# ═══════════════════════════════════════════════
# REST API — Status
# ═══════════════════════════════════════════════
@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    return StatusResponse(
        gateway=gateway_engine.status if gateway_engine else "stopped",
        bacnet_connected=bacnet_service.connected if bacnet_service else False,
        mqtt_connected=mqtt_service.connected if mqtt_service else False,
        active_mappings=len([m for m in config_manager.mappings if m.enabled]),
        discovered_devices=len(bacnet_service._devices) if bacnet_service else 0,
        uptime_seconds=gateway_engine.uptime if gateway_engine else 0,
    )


@app.get("/api/health")
async def get_health():
    return get_system_health()


@app.get("/api/devices/health")
async def get_devices_health():
    """Return bulk health status of all known BACnet devices.
    Merges: persisted known_devices + currently discovered + mapping devices.
    Devices are tracked via background ping even without mappings.
    """
    poll_status = gateway_engine.get_device_status() if gateway_engine else {}
    # Build a per-device point count index from mappings
    point_counts: dict[int, int] = {}
    for m in config_manager.mappings:
        point_counts[m.device_id] = point_counts.get(m.device_id, 0) + 1

    devices_out = []
    seen_ids: set[int] = set()

    # 1. Known devices (persisted across restarts — the main source)
    known = gateway_engine.get_known_devices() if gateway_engine else {}
    for dev_id, info in known.items():
        ps = poll_status.get(dev_id, {})
        devices_out.append({
            "device_id": dev_id,
            "name": info.get("name") or f"Device {dev_id}",
            "address": info.get("address"),
            "online": ps.get("online", None),        # None = never pinged yet
            "fail_count": ps.get("fail_count", 0),
            "last_seen": ps.get("last_seen"),
            "point_count": point_counts.get(dev_id, 0),
            # BMS server tracking flags
            "bms_queried": info.get("source") == "bms_server" or info.get("bms_queried", False),
        })
        seen_ids.add(dev_id)

    # 2. Currently discovered (in-memory) but not yet persisted
    if bacnet_service:
        for dev in bacnet_service.discovered_devices:
            dev_id = dev.device_id
            if dev_id in seen_ids:
                continue
            ps = poll_status.get(dev_id, {})
            devices_out.append({
                "device_id": dev_id,
                "name": dev.device_name or f"Device {dev_id}",
                "address": dev.address,
                "online": ps.get("online", True),
                "fail_count": ps.get("fail_count", 0),
                "last_seen": ps.get("last_seen"),
                "point_count": point_counts.get(dev_id, 0),
            })
            seen_ids.add(dev_id)

    # 3. Devices in mappings but never discovered
    for dev_id, count in point_counts.items():
        if dev_id not in seen_ids:
            ps = poll_status.get(dev_id, {})
            devices_out.append({
                "device_id": dev_id,
                "name": f"Device {dev_id}",
                "address": None,
                "online": ps.get("online", False),
                "fail_count": ps.get("fail_count", 0),
                "last_seen": ps.get("last_seen"),
                "point_count": count,
            })

    devices_out.sort(key=lambda d: (d["online"] is False, d["online"] is None, d["device_id"]))
    return {"devices": devices_out, "total": len(devices_out)}


# ═══════════════════════════════════════════════
# REST API — Anomaly Monitor
# ═══════════════════════════════════════════════
def _get_anomaly() -> "AnomalyEngine | None":
    """Helper to get the AnomalyEngine from the GatewayEngine."""
    return getattr(gateway_engine, "_anomaly", None) if gateway_engine else None


@app.get("/api/anomaly/rules")
async def list_anomaly_rules():
    ae = _get_anomaly()
    return {"rules": ae.get_rules() if ae else []}


@app.post("/api/anomaly/rules")
async def create_anomaly_rule(rule: dict):
    ae = _get_anomaly()
    if not ae:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "AnomalyEngine not initialized"}, status_code=500)
    created = ae.add_rule(rule)
    return {"rule": created.__dict__}


@app.put("/api/anomaly/rules/{rule_id}")
async def update_anomaly_rule(rule_id: str, updates: dict):
    ae = _get_anomaly()
    if not ae:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "AnomalyEngine not initialized"}, status_code=500)
    updated = ae.update_rule(rule_id, updates)
    if not updated:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Rule not found"}, status_code=404)
    return {"rule": updated.__dict__}


@app.delete("/api/anomaly/rules/{rule_id}")
async def delete_anomaly_rule(rule_id: str):
    ae = _get_anomaly()
    if not ae:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "AnomalyEngine not initialized"}, status_code=500)
    deleted = ae.delete_rule(rule_id)
    return {"deleted": deleted}


@app.get("/api/anomaly/active")
async def get_active_alarms():
    ae = _get_anomaly()
    return {"alarms": ae.get_active_alarms() if ae else []}


# ═══════════════════════════════════════════════
# REST API — Gateway Control
# ═══════════════════════════════════════════════
@app.post("/api/gateway/start")
async def start_gateway():
    if not bacnet_service or not gateway_engine:
        return JSONResponse({"error": "Services not initialised"}, status_code=500)
    try:
        if not bacnet_service.connected:
            await bacnet_service.start()
        await gateway_engine.start()
        return {"status": "started"}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/gateway/stop")
async def stop_gateway():
    if gateway_engine:
        await gateway_engine.stop()
    return {"status": "stopped"}

# ═══════════════════════════════════════════════
# REST API — BACnet Config
# ═══════════════════════════════════════════════
@app.get("/api/bacnet/config")
async def get_bacnet_config():
    return config_manager.config.bacnet.model_dump()


@app.put("/api/bacnet/config")
async def update_bacnet_config(cfg: BacnetConfig):
    """Update BACnet config (IP, mask, port). Takes effect on next Discover."""
    config_manager.config.bacnet = cfg
    config_manager.save()
    return {"status": "updated", "message": f"BACnet config saved: {cfg.ip}/{cfg.mask}. Click Discover to apply."}


@app.get("/api/bacnet/interfaces")
async def list_network_interfaces():
    """List available network interfaces with IPs."""
    import subprocess, json as _json
    interfaces = []
    try:
        result = subprocess.run(
            ["ip", "-4", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        data = _json.loads(result.stdout)
        for iface in data:
            name = iface.get("ifname", "")
            state = iface.get("operstate", "UNKNOWN")
            for addr in iface.get("addr_info", []):
                ip = addr.get("local", "")
                prefix = str(addr.get("prefixlen", "24"))
                if ip and not ip.startswith("127."):
                    interfaces.append({"interface": name, "ip": ip, "mask": prefix, "state": state})
    except Exception as exc:
        logger.warning("Could not list interfaces: %s", exc)
    return {"interfaces": interfaces}


# ═══════════════════════════════════════════════
# REST API — BACnet Discovery & Read/Write
# ═══════════════════════════════════════════════
@app.post("/api/bacnet/discover")
async def discover_devices(req: DiscoveryRequest | None = None):
    global bacnet_service
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    try:
        # Start BACnet if not connected yet
        if not bacnet_service.connected:
            # Stop any stale instance first
            await bacnet_service.stop()
            import asyncio
            await asyncio.sleep(1)

            # Rebuild with latest config
            bacnet_service = BacnetService(config_manager.config.bacnet)
            if gateway_engine:
                gateway_engine._bacnet = bacnet_service

            await bacnet_service.start()

        # Run discovery with scan mode parameters
        timeout = req.timeout if req else 10
        scan_mode = req.scan_mode if req else "full"
        devices = await bacnet_service.discover_devices(
            timeout=timeout,
            scan_mode=scan_mode,
            low_id=req.low_id if req else None,
            high_id=req.high_id if req else None,
            device_id=req.device_id if req else None,
        )
        # Register discovered devices for persistent tracking + background ping
        if gateway_engine and devices:
            gateway_engine.register_discovered_devices(devices)
        return {"devices": [d.model_dump() for d in devices], "scan_mode": scan_mode}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/bacnet/devices")
async def list_devices(source: str = "all"):
    """
    Return known BACnet devices.
    source=all (default): merge registry (persistent) + live discovered devices
    source=live: only currently discovered in-memory
    source=registry: only from persistent registry
    """
    alive_ids: set[int] = set()
    live_devices: dict[int, dict] = {}

    if bacnet_service and source in ("all", "live"):
        for d in bacnet_service.discovered_devices:
            dev_dict = d.model_dump()
            dev_dict["live"] = True
            live_devices[d.device_id] = dev_dict
            alive_ids.add(d.device_id)

    result_map: dict[int, dict] = {}

    # Start from registry (persistent baseline)
    if device_registry and source in ("all", "registry"):
        for dev in device_registry.all_devices():
            result_map[dev["device_id"]] = {
                **dev,
                "live": dev["device_id"] in alive_ids,
                "from_registry": True,
            }

    # Merge live — live data wins (fresher)
    for dev_id, dev in live_devices.items():
        if dev_id in result_map:
            result_map[dev_id].update(dev)
        else:
            result_map[dev_id] = dev
        # Save newly discovered device to registry in background
        if device_registry:
            device_registry.upsert_device(
                dev_id,
                device_name=dev.get("device_name", ""),
                address=dev.get("address", ""),
                vendor_name=dev.get("vendor_name", ""),
                model_name=dev.get("model_name", ""),
                network_id=dev.get("network_id", ""),
            )

    devices_out = sorted(result_map.values(), key=lambda d: d.get("device_id", 0))
    return {"devices": devices_out, "total": len(devices_out), "live_count": len(alive_ids)}


@app.get("/api/bacnet/configured-devices")
async def configured_devices():
    """Return devices referenced in mappings with online/offline status."""
    mappings = config_manager.mappings
    device_map: dict[int, dict] = {}
    for m in mappings:
        dev = device_map.setdefault(m.device_id, {
            "device_id": m.device_id,
            "point_count": 0,
            "online": False,
            "address": None,
            "last_updated": None,
            "fail_count": 0,
        })
        dev["point_count"] += 1
        if m.last_updated and (not dev["last_updated"] or m.last_updated > dev["last_updated"]):
            dev["last_updated"] = m.last_updated

    # Use real-time status from gateway engine if available
    if gateway_engine:
        rt_status = gateway_engine.get_device_status()
        for dev_id, info in device_map.items():
            if dev_id in rt_status:
                st = rt_status[dev_id]
                info["online"] = st["online"]
                info["address"] = st.get("address")
                info["fail_count"] = st.get("fail_count", 0)
                info["last_seen"] = st.get("last_seen")
            elif bacnet_service:
                addr = bacnet_service.get_device_address(dev_id)
                if addr:
                    info["address"] = addr
    elif bacnet_service:
        for dev_id, info in device_map.items():
            addr = bacnet_service.get_device_address(dev_id)
            if addr:
                info["online"] = True
                info["address"] = addr

    return {"devices": list(device_map.values())}


@app.get("/api/events")
async def get_events(
    event_type: str | None = None,
    device_id: int | None = None,
    severity: str | None = None,
    from_ts: str | None = None,   # ISO8601 start time
    to_ts: str | None = None,     # ISO8601 end time
    search: str | None = None,    # substring search in message
    limit: int = 200,
    offset: int = 0,
):
    """Query event log with optional filters."""
    if not history_store:
        return {"events": [], "total": 0}

    # Extended query with from_ts, to_ts, search
    if not history_store._conn:
        return {"events": [], "total": 0}

    sql = """SELECT id, timestamp, event_type, device_id, mapping_id,
                    severity, message, data_json
             FROM event_log WHERE 1=1"""
    params: list = []
    if event_type:
        sql += " AND event_type = ?"; params.append(event_type)
    if device_id is not None:
        sql += " AND device_id = ?"; params.append(device_id)
    if severity:
        sql += " AND severity = ?"; params.append(severity)
    if from_ts:
        sql += " AND timestamp >= ?"; params.append(from_ts)
    if to_ts:
        sql += " AND timestamp <= ?"; params.append(to_ts)
    if search:
        sql += " AND message LIKE ?"; params.append(f"%{search}%")

    # Count total (wrap in a subquery)
    count_sql = f"SELECT COUNT(*) FROM ({sql}) _sub"
    total = history_store._conn.execute(count_sql, params).fetchone()[0]


    sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = history_store._conn.execute(sql, params).fetchall()

    import json as _json
    events = [
        {
            "id": r[0], "timestamp": r[1], "event_type": r[2],
            "device_id": r[3], "mapping_id": r[4], "severity": r[5],
            "message": r[6], "data": _json.loads(r[7]) if r[7] else None,
        }
        for r in rows
    ]
    return {"events": events, "total": total}


@app.get("/api/events/online-chart")
async def get_online_chart(hours: int = 24):
    """Return hourly online/offline counts for the past N hours.
    Used for the Dashboard stability chart.
    """
    if not history_store or not history_store._conn:
        return {"series": []}

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    # Count device_online and device_offline events per hour slot
    rows = history_store._conn.execute(
        """SELECT strftime('%Y-%m-%dT%H:00:00', timestamp) as hour,
                  event_type, COUNT(*) as cnt
           FROM event_log
           WHERE event_type IN ('device_online','device_offline')
             AND timestamp >= ?
           GROUP BY hour, event_type
           ORDER BY hour ASC""",
        (since.isoformat(),),
    ).fetchall()

    # Build hourly map
    slots: dict = {}
    for row in rows:
        h, etype, cnt = row
        if h not in slots:
            slots[h] = {"time": h, "online": 0, "offline": 0}
        if etype == "device_online":
            slots[h]["online"] = cnt
        else:
            slots[h]["offline"] = cnt

    return {"series": list(slots.values())}



@app.get("/api/devices/{device_id}/offline-history")
async def get_device_offline_history(device_id: int, limit: int = 200):
    """Return paired offline↔online incidents for a device.
    Each incident: { offline_at, online_at, duration_s, duration_text }
    """
    if not history_store or history_store._conn is None:
        return {"incidents": [], "offline_count": 0}

    rows = history_store._conn.execute(
        """SELECT event_type, timestamp FROM event_log
           WHERE device_id = ? AND event_type IN ('device_offline','device_online')
           ORDER BY timestamp ASC
           LIMIT ?""",
        (device_id, limit * 2),
    ).fetchall()

    incidents = []
    i = 0
    while i < len(rows):
        etype, ts = rows[i]
        if etype == "device_offline":
            offline_at = ts
            online_at = None
            # Look for the next device_online
            j = i + 1
            while j < len(rows):
                if rows[j][0] == "device_online":
                    online_at = rows[j][1]
                    i = j  # skip forward
                    break
                j += 1

            # Compute duration
            duration_s = None
            duration_text = "Still offline"
            if online_at:
                try:
                    from datetime import datetime, timezone
                    def parse_ts(s):
                        # Handle both UTC offset and Z suffix
                        s = s.replace("Z", "+00:00")
                        return datetime.fromisoformat(s)
                    dt_off = parse_ts(offline_at)
                    dt_on = parse_ts(online_at)
                    duration_s = int((dt_on - dt_off).total_seconds())
                    if duration_s < 60:
                        duration_text = f"{duration_s}s"
                    elif duration_s < 3600:
                        duration_text = f"{duration_s // 60}m {duration_s % 60}s"
                    else:
                        h = duration_s // 3600
                        m = (duration_s % 3600) // 60
                        duration_text = f"{h}h {m}m"
                except Exception:
                    pass

            incidents.append({
                "offline_at": offline_at,
                "online_at": online_at,
                "duration_s": duration_s,
                "duration_text": duration_text,
            })
        i += 1

    # Return most recent first
    incidents.reverse()
    return {"incidents": incidents[:limit], "offline_count": len(incidents)}



@app.get("/api/bacnet/devices/{device_id}/objects")
async def list_objects(device_id: int, refresh: bool = False):
    """
    Return object list for a device.
    - By default: serve from registry cache (no BACnet read = network-friendly)
    - If refresh=true OR cache empty: read from BACnet network and save to registry
    """
    # Serve from registry cache first
    if device_registry and not refresh:
        cached_objs = device_registry.get_objects(device_id)
        if cached_objs:
            return {"objects": cached_objs, "from_cache": True, "cache_size": len(cached_objs)}

    # Need live read from BACnet
    if not bacnet_service:
        # If no BACnet service, try registry anyway
        if device_registry:
            cached_objs = device_registry.get_objects(device_id)
            if cached_objs:
                return {"objects": cached_objs, "from_cache": True}
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)

    address = bacnet_service.get_device_address(device_id)
    if not address:
        # Device not live — check registry for address
        reg_dev = device_registry.get_device(device_id) if device_registry else None
        if reg_dev and reg_dev.get("address"):
            address = reg_dev["address"]
        else:
            return JSONResponse(
                {"error": f"Device {device_id} not reachable. Start gateway and scan first."},
                status_code=404,
            )

    objects = await bacnet_service.read_object_list(address, device_id)
    objects_dicts = [o.model_dump() for o in objects]

    # Save to registry
    if device_registry and objects_dicts:
        device_registry.upsert_objects(device_id, objects_dicts)

    return {"objects": objects_dicts, "from_cache": False}


@app.post("/api/bacnet/devices/{device_id}/objects/refresh")
async def refresh_device_objects(device_id: int):
    """Force re-read objects from BACnet network and update registry."""
    return await list_objects(device_id, refresh=True)


@app.get("/api/bacnet/devices/{device_id}/name")
async def read_device_name(device_id: int):
    """Read a device's objectName on-demand (cached after first read)."""
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    name = await bacnet_service.read_device_name(device_id)
    # Persist name to registry so it shows in device list
    if device_registry and not name.startswith("Device "):
        device_registry.upsert_device(device_id, device_name=name)
    return {"device_id": device_id, "name": name}


@app.post("/api/bacnet/devices/names")
async def read_device_names(req: dict):
    """Read names for a batch of device IDs (sequential, keeps BAC0 queue clear)."""
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    device_ids = req.get("device_ids", [])
    if not device_ids or len(device_ids) > 20:
        return JSONResponse({"error": "Provide 1-20 device_ids"}, status_code=400)
    names = await bacnet_service.read_device_names_batch(device_ids)
    return {"names": names}


@app.get("/api/debug/bac0-devices")
async def debug_bac0_devices():
    """Show BAC0 discoveredDevices structure to find which attribute holds the objectName."""
    if not bacnet_service or not bacnet_service._network:
        return {"error": "No BAC0 network"}
    result = []
    try:
        for addr, dev in list(bacnet_service._network.discoveredDevices.items())[:5]:
            attrs = {k: str(getattr(dev, k, "N/A"))[:50]
                     for k in ["objectName", "description", "instance", "address",
                                "deviceInstanceRangeHighLimit", "deviceInstanceRangeLowLimit",
                                "vendorName", "modelName"]
                     if hasattr(dev, k)}
            result.append({"address": str(addr), "attrs": attrs, "type": type(dev).__name__})
    except Exception as exc:
        return {"error": str(exc)}
    return {"count": len(bacnet_service._network.discoveredDevices), "samples": result}


@app.get("/api/bacnet/diag/{device_id}")
async def diagnose_device(device_id: int):
    """Diagnose BAC0 read issues for a specific device."""
    if not bacnet_service or not bacnet_service._network:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)

    device = bacnet_service._devices.get(device_id)
    if not device:
        return JSONResponse({"error": f"Device {device_id} not found"}, status_code=404)

    net = bacnet_service._network
    addr = device.address
    results = {"device_id": device_id, "address": addr, "tests": {}}

    # 1. Check device_info_cache
    try:
        from bacpypes3.pdu import Address
        device_address = Address(addr)
        dic = await net.this_application.app.device_info_cache.get_device_info(device_address)
        results["tests"]["device_info_cache"] = str(dic) if dic else "EMPTY"
    except Exception as e:
        results["tests"]["device_info_cache"] = f"ERROR: {e}"

    # 2. Try unicast WHO-IS
    try:
        from bacpypes3.pdu import Address
        iam_resp = await net.this_application.app.who_is(address=Address(addr))
        results["tests"]["unicast_whois"] = str(iam_resp) if iam_resp else "NO RESPONSE"
    except Exception as e:
        results["tests"]["unicast_whois"] = f"ERROR: {e}"

    # 3. Try BAC0 read with verbose error
    try:
        val = await net.read(f"{addr} device {device_id} objectName")
        results["tests"]["read_objectName"] = str(val) if val else "EMPTY"
    except Exception as e:
        results["tests"]["read_objectName"] = f"ERROR: {type(e).__name__}: {e}"

    # 4. Try readMultiple
    try:
        val = await net.readMultiple(f"{addr} device {device_id} objectName objectList")
        results["tests"]["readMultiple"] = str(val) if val else "EMPTY"
    except Exception as e:
        results["tests"]["readMultiple"] = f"ERROR: {type(e).__name__}: {e}"

    return results


def _resolve_address(device_id: int) -> str | None:
    """Resolve BACnet address for device_id.
    First checks live in-memory _devices, then falls back to persistent device_registry.
    """
    if bacnet_service:
        addr = bacnet_service.get_device_address(device_id)
        if addr:
            return addr
    if device_registry:
        reg = device_registry.get_device(device_id)
        if reg:
            return reg.get("address") if isinstance(reg, dict) else getattr(reg, "address", None)
    return None


@app.post("/api/bacnet/write")
async def write_bacnet(req: WriteRequest):
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    address = _resolve_address(req.device_id)
    if not address:
        return JSONResponse({"error": f"Device {req.device_id} not found"}, status_code=404)
    ok, err = await bacnet_service.write_object(
        address, req.object_type, req.object_instance, req.value, req.priority
    )
    if ok:
        return {"success": True, "priority": req.priority}
    return JSONResponse({"success": False, "error": err or "Write rejected by device"}, status_code=200)


@app.post("/api/bacnet/release")
async def release_bacnet(req: ReleaseRequest):
    """Release (null) a single priority or all priorities 1–16."""
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    address = _resolve_address(req.device_id)
    if not address:
        return JSONResponse({"error": f"Device {req.device_id} not found"}, status_code=404)

    if str(req.priority).lower() == "all":
        results = await bacnet_service.release_all_priorities(
            address, req.object_type, req.object_instance
        )
        success_count = sum(1 for v in results.values() if v)
        return {"success": True, "released": success_count, "total": 16, "results": results}
    else:
        pri = int(req.priority)
        ok = await bacnet_service.release_priority(
            address, req.object_type, req.object_instance, pri
        )
        return {"success": ok, "priority": pri}


@app.get("/api/bacnet/priority_array/{device_id}/{object_type}/{object_instance}")
async def read_priority_array(device_id: int, object_type: str, object_instance: int):
    """Read the 16-level priority array of a BACnet object."""
    if not bacnet_service:
        return JSONResponse({"error": "BACnet service not available"}, status_code=500)
    address = _resolve_address(device_id)
    if not address:
        return JSONResponse({"error": f"Device {device_id} not found"}, status_code=404)
    pa = await bacnet_service.read_priority_array(address, object_type, object_instance)
    pv = await bacnet_service.read_object(address, object_type, object_instance)
    return {"present_value": pv, "priority_array": pa}


# ═══════════════════════════════════════════════
# REST API — MQTT
# ═══════════════════════════════════════════════
@app.get("/api/mqtt/config")
async def get_mqtt_config():
    return config_manager.config.mqtt.model_dump()


@app.put("/api/mqtt/config")
async def update_mqtt_config(cfg: MqttConfig):
    config_manager.update_mqtt(**cfg.model_dump())
    if mqtt_service:
        mqtt_service.update_config(cfg)
    return {"status": "updated"}


@app.post("/api/mqtt/test")
async def test_mqtt(req: MqttTestRequest):
    result = MqttService.test_connection(
        host=req.broker_host,
        port=req.broker_port,
        username=req.username,
        password=req.password,
        use_tls=req.use_tls,
    )
    return result


# ═══════════════════════════════════════════════
# REST API — Groups
# ═══════════════════════════════════════════════
@app.get("/api/groups")
async def list_groups():
    return {"groups": [g.model_dump() for g in config_manager.groups]}


@app.post("/api/groups")
async def create_group(group: GroupConfig):
    added = config_manager.add_group(group)
    return added.model_dump()


@app.put("/api/groups/{group_id}")
async def update_group(group_id: str, group: dict[str, Any]):
    updated = config_manager.update_group(group_id, **group)
    if not updated:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    return updated.model_dump()


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str):
    removed = config_manager.remove_group(group_id)
    if not removed:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    return {"status": "deleted"}


# ═══════════════════════════════════════════════
# REST API — Webhooks
# ═══════════════════════════════════════════════
@app.get("/api/webhooks")
async def list_webhooks():
    webhooks = getattr(config_manager.config, "webhooks", [])
    return {"webhooks": [w.model_dump() for w in webhooks]}


@app.post("/api/webhooks")
async def create_webhook(wh: WebhookConfig):
    from uuid import uuid4
    if not wh.id:
        wh.id = str(uuid4())
    webhooks = getattr(config_manager.config, "webhooks", [])
    webhooks.append(wh)
    config_manager.config.webhooks = webhooks
    config_manager.save()
    return wh.model_dump()


@app.put("/api/webhooks/{wh_id}")
async def update_webhook(wh_id: str, data: dict):
    webhooks = getattr(config_manager.config, "webhooks", [])
    for i, w in enumerate(webhooks):
        if w.id == wh_id:
            updated = w.model_copy(update=data)
            webhooks[i] = updated
            config_manager.config.webhooks = webhooks
            config_manager.save()
            return updated.model_dump()
    return JSONResponse({"error": "Webhook not found"}, status_code=404)


@app.delete("/api/webhooks/{wh_id}")
async def delete_webhook(wh_id: str):
    webhooks = getattr(config_manager.config, "webhooks", [])
    before = len(webhooks)
    config_manager.config.webhooks = [w for w in webhooks if w.id != wh_id]
    if len(config_manager.config.webhooks) == before:
        return JSONResponse({"error": "Webhook not found"}, status_code=404)
    config_manager.save()
    return {"status": "deleted"}


@app.post("/api/webhooks/{wh_id}/test")
async def test_webhook(wh_id: str):
    if not webhook_service:
        return JSONResponse({"error": "Webhook service not initialised"}, status_code=503)
    webhooks = getattr(config_manager.config, "webhooks", [])
    wh = next((w for w in webhooks if w.id == wh_id), None)
    if not wh:
        return JSONResponse({"error": "Webhook not found"}, status_code=404)
    result = await webhook_service.send_test(wh)
    return result


# ═══════════════════════════════════════════════
# REST API — Authentication
# ═══════════════════════════════════════════════
@app.post("/api/auth/login")
async def login(body: dict):
    """Public endpoint — returns JWT token if credentials valid."""
    username = body.get("username", "").strip()
    password = body.get("password", "")
    users = getattr(config_manager.config, "users", [])

    # Auth disabled mode
    if not users:
        return {"token": None, "role": "admin", "username": "anonymous", "auth_enabled": False}

    user = next((u for u in users if u.username == username and u.enabled), None)
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_token(user.id, user.username, user.role)
    return {"token": token, "role": user.role, "username": user.username, "auth_enabled": True}


@app.get("/api/auth/me")
async def auth_me(payload: dict = Depends(require_auth)):
    """Return current user info from token."""
    return {"username": payload.get("username"), "role": payload.get("role"),
            "auth_enabled": bool(getattr(config_manager.config, "users", []))}


@app.get("/api/auth/status")
async def auth_status():
    """Public — tells frontend whether auth is enabled."""
    return {"auth_enabled": bool(getattr(config_manager.config, "users", []))}


# ── User management (Admin only) ──────────────
@app.get("/api/users")
async def list_users(_: dict = Depends(require_admin)):
    users = getattr(config_manager.config, "users", [])
    return {"users": [{"id": u.id, "username": u.username, "role": u.role, "enabled": u.enabled} for u in users]}


@app.post("/api/users")
async def create_user(body: dict, _: dict = Depends(require_admin)):
    from uuid import uuid4
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    role = body.get("role", "viewer")
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)
    if role not in ("admin", "operator", "viewer"):
        return JSONResponse({"error": "role must be admin, operator, or viewer"}, status_code=400)
    users = list(getattr(config_manager.config, "users", []))
    if any(u.username == username for u in users):
        return JSONResponse({"error": "Username already exists"}, status_code=409)
    from backend.models import UserConfig
    new_user = UserConfig(id=str(uuid4()), username=username, hashed_password=hash_password(password), role=role)
    users.append(new_user)
    config_manager.config.users = users
    config_manager.save()
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role, "enabled": new_user.enabled}


@app.put("/api/users/{user_id}")
async def update_user(user_id: str, body: dict, _: dict = Depends(require_admin)):
    users = list(getattr(config_manager.config, "users", []))
    for i, u in enumerate(users):
        if u.id == user_id:
            if "role" in body and body["role"] in ("admin", "operator", "viewer"):
                users[i] = u.model_copy(update={"role": body["role"]})
            if "enabled" in body:
                users[i] = users[i].model_copy(update={"enabled": bool(body["enabled"])})
            if "password" in body and body["password"]:
                users[i] = users[i].model_copy(update={"hashed_password": hash_password(body["password"])})
            config_manager.config.users = users
            config_manager.save()
            u2 = users[i]
            return {"id": u2.id, "username": u2.username, "role": u2.role, "enabled": u2.enabled}
    return JSONResponse({"error": "User not found"}, status_code=404)


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str, payload: dict = Depends(require_admin)):
    users = list(getattr(config_manager.config, "users", []))
    before = len(users)
    users = [u for u in users if u.id != user_id]
    if len(users) == before:
        return JSONResponse({"error": "User not found"}, status_code=404)
    config_manager.config.users = users
    config_manager.save()
    return {"status": "deleted"}


# ═══════════════════════════════════════════════
# REST API — Schedules
# ═══════════════════════════════════════════════
@app.get("/api/schedules")
async def list_schedules():
    return {"schedules": [s.model_dump() for s in config_manager.schedules]}


@app.post("/api/schedules")
async def create_schedule(sched: ScheduleEntry):
    added = config_manager.add_schedule(sched)
    return added.model_dump()


@app.put("/api/schedules/{sched_id}")
async def update_schedule(sched_id: str, body: dict[str, Any]):
    updated = config_manager.update_schedule(sched_id, **body)
    if not updated:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return updated.model_dump()


@app.delete("/api/schedules/{sched_id}")
async def delete_schedule(sched_id: str):
    removed = config_manager.remove_schedule(sched_id)
    if not removed:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return {"status": "deleted"}


@app.get("/api/schedules/status")
async def get_schedule_status():
    """Return last run status for all schedules."""
    if not scheduler_service:
        return {"status": {}}
    return {"status": scheduler_service.get_last_run_status()}


@app.post("/api/schedules/{sched_id}/run")
async def run_schedule_now(sched_id: str):
    """Manually trigger a schedule immediately."""
    if not scheduler_service:
        return JSONResponse({"error": "Scheduler not initialised"}, status_code=503)
    result = await scheduler_service.run_now(sched_id)
    return result


# ═══════════════════════════════════════════════
# REST API — Data Export
# ═══════════════════════════════════════════════
import csv
import io

@app.get("/api/export/history.csv")
async def export_history_csv(
    from_ts: str = "",
    to_ts: str = "",
    mapping_id: str = "",  # comma-separated IDs; empty = all
):
    """Export point_history as CSV stream. Params: from_ts, to_ts (ISO), mapping_id."""
    if not history_store:
        return JSONResponse({"error": "History store not available"}, status_code=503)

    query = "SELECT mapping_id, value, value_text, timestamp FROM point_history WHERE 1=1"
    params: list = []
    if from_ts:
        query += " AND timestamp >= ?"; params.append(from_ts)
    if to_ts:
        query += " AND timestamp <= ?"; params.append(to_ts)
    if mapping_id:
        ids = [x.strip() for x in mapping_id.split(",") if x.strip()]
        placeholders = ",".join("?" * len(ids))
        query += f" AND mapping_id IN ({placeholders})"; params.extend(ids)
    query += " ORDER BY timestamp ASC LIMIT 200000"

    rows = history_store._conn.execute(query, params).fetchall()

    # Build mapping label lookup
    label_map = {m.id: (m.label or f"{m.object_type}:{m.object_instance}") for m in config_manager.mappings}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "mapping_id", "label", "value", "value_text"])
    for mapping_id_row, value, value_text, timestamp in rows:
        writer.writerow([timestamp, mapping_id_row, label_map.get(mapping_id_row, ""), value, value_text])

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=point_history.csv"},
    )


@app.get("/api/export/events.csv")
async def export_events_csv(from_ts: str = "", to_ts: str = "", event_type: str = ""):
    """Export event_log as CSV."""
    if not history_store:
        return JSONResponse({"error": "History store not available"}, status_code=503)

    query = "SELECT timestamp, event_type, device_id, mapping_id, severity, message FROM event_log WHERE 1=1"
    params: list = []
    if from_ts:
        query += " AND timestamp >= ?"; params.append(from_ts)
    if to_ts:
        query += " AND timestamp <= ?"; params.append(to_ts)
    if event_type:
        query += " AND event_type = ?"; params.append(event_type)
    query += " ORDER BY timestamp ASC LIMIT 50000"

    rows = history_store._conn.execute(query, params).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "event_type", "device_id", "mapping_id", "severity", "message"])
    writer.writerows(rows)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=event_log.csv"},
    )


@app.get("/api/export/summary")
async def export_summary():
    """Return date range and counts for the export UI."""
    if not history_store:
        return {"history_count": 0, "event_count": 0, "oldest": None, "newest": None}

    c = history_store._conn
    h = c.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM point_history").fetchone()
    e = c.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM event_log").fetchone()
    return {
        "history_count": h[0], "history_oldest": h[1], "history_newest": h[2],
        "event_count": e[0], "event_oldest": e[1], "event_newest": e[2],
    }


# ═══════════════════════════════════════════════
# REST API — Mappings
# ═══════════════════════════════════════════════
@app.get("/api/mappings")
async def list_mappings():
    return {"mappings": [m.model_dump() for m in config_manager.mappings]}


@app.post("/api/mappings")
async def create_mapping(mapping: PointMapping):
    ram_check = check_ram_for_new_points(1)
    created = config_manager.add_mapping(mapping)
    result = {"mapping": created.model_dump()}
    if ram_check.get("warning"):
        result["ram_warning"] = ram_check["warning"]
        result["ram_percent"] = ram_check["ram_percent"]
        result["ram_status"] = ram_check["ram_status"]
    return result


@app.post("/api/mappings/bulk")
async def create_mappings_bulk(body: dict[str, Any]):
    """Bulk-create multiple mappings at once. Body: {mappings: [...]}"""
    items = body.get("mappings", [])
    ram_check = check_ram_for_new_points(len(items))
    if not ram_check["allowed"]:
        return JSONResponse(
            {"error": ram_check["warning"], "ram_percent": ram_check["ram_percent"],
             "ram_status": ram_check["ram_status"]},
            status_code=429,
        )
    created = []
    for m in items:
        try:
            mapping = PointMapping(**m)
            result = config_manager.add_mapping(mapping)
            created.append(result.model_dump())
        except Exception as exc:
            logger.warning("Bulk map skip: %s", exc)
    resp = {"created": len(created), "mappings": created}
    if ram_check.get("warning"):
        resp["ram_warning"] = ram_check["warning"]
        resp["ram_percent"] = ram_check["ram_percent"]
    return resp


@app.put("/api/mappings/{mapping_id}")
async def update_mapping(mapping_id: str, body: dict[str, Any]):
    updated = config_manager.update_mapping(mapping_id, **body)
    if not updated:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)
    return {"mapping": updated.model_dump()}


@app.delete("/api/mappings/{mapping_id}")
async def delete_mapping(mapping_id: str):
    removed = config_manager.remove_mapping(mapping_id)
    if not removed:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)
    return {"status": "deleted"}


@app.put("/api/mappings/{mapping_id}/alarm")
async def update_alarm_config(mapping_id: str, body: dict[str, Any]):
    """Set alarm thresholds for a specific mapping."""
    m = config_manager.get_mapping(mapping_id)
    if not m:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)
    try:
        alarm_cfg = AlarmConfig(**body)
        config_manager.update_mapping(mapping_id, alarm_config=alarm_cfg.model_dump())
        return {"status": "ok", "alarm_config": alarm_cfg.model_dump()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Export / Import Mappings ──────────────────
@app.get("/api/mappings/export")
async def export_mappings():
    """Export all mappings as JSON array."""
    return {"mappings": [m.model_dump() for m in config_manager.mappings]}


@app.post("/api/mappings/import")
async def import_mappings(body: dict[str, Any]):
    """Import mappings with ID-based upsert.
    New IDs → add, existing IDs → update."""
    incoming = body.get("mappings", [])
    if not incoming:
        return JSONResponse({"error": "No mappings provided"}, status_code=400)

    added, updated, errors = 0, 0, 0
    for item in incoming:
        try:
            mid = item.get("id", "")
            existing = config_manager.get_mapping(mid) if mid else None
            if existing:
                # Update existing
                for k, v in item.items():
                    if k != "id" and hasattr(existing, k):
                        setattr(existing, k, v)
                updated += 1
            else:
                # Create new
                from backend.models import PointMapping
                m = PointMapping(**{k: v for k, v in item.items()
                                   if k in PointMapping.model_fields})
                if mid:
                    m.id = mid
                config_manager.add_mapping(m)
                added += 1
        except Exception as exc:
            logger.warning("Import error for item: %s", exc)
            errors += 1

    config_manager.save()
    return {"added": added, "updated": updated, "errors": errors,
            "total": len(config_manager.mappings)}


# ═══════════════════════════════════════════════
# REST API — Reports Export
# ═══════════════════════════════════════════════
@app.get("/api/reports/export")
async def export_report(
    format: str = "csv",
    start: str | None = None,
    end: str | None = None,
    point_ids: str | None = None,
    group: str | None = None,
):
    """Export point history as CSV or JSON.
    - format: csv | json
    - point_ids: comma-separated mapping IDs
    - group: filter by group name
    """
    if not history_store:
        return JSONResponse({"error": "History store not available"}, status_code=500)

    # Resolve point IDs
    ids = None
    if point_ids:
        ids = [x.strip() for x in point_ids.split(",") if x.strip()]
    elif group:
        ids = [m.id for m in config_manager.mappings if m.group == group]

    data = history_store.export_range(mapping_ids=ids, start=start, end=end)

    # Build label lookup
    label_map = {m.id: (m.label or f"{m.object_type}:{m.object_instance}") for m in config_manager.mappings}

    if format == "csv":
        import csv
        import io
        from starlette.responses import StreamingResponse

        def generate():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Timestamp", "Point ID", "Label", "Value"])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for row in data:
                writer.writerow([
                    row["timestamp"],
                    row["mapping_id"],
                    label_map.get(row["mapping_id"], row["mapping_id"]),
                    row["value"],
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=bacnet_report.csv"},
        )
    else:
        # JSON format
        for row in data:
            row["label"] = label_map.get(row["mapping_id"], row["mapping_id"])
        return {"records": data, "count": len(data)}


@app.get("/api/mappings/{mapping_id}/properties")
async def get_mapping_properties(mapping_id: str):
    """Read extended BACnet properties for a mapping's object."""
    mapping = config_manager.get_mapping(mapping_id)
    if not mapping:
        return JSONResponse({"error": "Mapping not found"}, status_code=404)

    if not bacnet_service or not bacnet_service.connected:
        return JSONResponse({"error": "BACnet not connected"}, status_code=503)

    # Find device address
    device = bacnet_service._devices.get(mapping.device_id)
    if not device:
        return JSONResponse({"error": f"Device {mapping.device_id} not found. Run discovery first."}, status_code=404)

    address = device.address if hasattr(device, 'address') else device.get("address", "")
    try:
        props = await bacnet_service.read_object_properties(
            address, mapping.object_type, mapping.object_instance
        )
        # Save to mapping
        if props.get("units"):
            mapping.units = props["units"]
        if props.get("description"):
            mapping.description = props["description"]
        if props.get("state_text"):
            mapping.state_text = props["state_text"]
        if props.get("active_text"):
            mapping.active_text = props["active_text"]
        if props.get("inactive_text"):
            mapping.inactive_text = props["inactive_text"]
        config_manager.save()

        return {"properties": props, "mapping": mapping.model_dump()}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ═══════════════════════════════════════════════
# REST API — Chart Configuration
# ═══════════════════════════════════════════════
@app.get("/api/charts")
async def get_charts():
    return {"charts": [c.model_dump() for c in config_manager.charts]}


@app.post("/api/charts")
async def create_chart(chart: ChartConfig):
    created = config_manager.add_chart(chart)
    return {"chart": created.model_dump()}


@app.put("/api/charts/{chart_id}")
async def update_chart(chart_id: str, body: dict[str, Any]):
    updated = config_manager.update_chart(chart_id, **body)
    if not updated:
        return JSONResponse({"error": "Chart not found"}, status_code=404)
    return {"chart": updated.model_dump()}


@app.delete("/api/charts/{chart_id}")
async def delete_chart(chart_id: str):
    removed = config_manager.remove_chart(chart_id)
    if not removed:
        return JSONResponse({"error": "Chart not found"}, status_code=404)
    return {"status": "deleted"}

# ═══════════════════════════════════════════════
# REST API — Point History
# ═══════════════════════════════════════════════
@app.get("/api/history/stats/overview")
async def get_history_stats():
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)
    return history_store.get_stats()


@app.get("/api/history/config")
async def get_history_config():
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)
    return history_store.get_config()


@app.put("/api/history/config")
async def update_history_config(body: dict[str, Any]):
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)
    return history_store.update_config(**body)


@app.post("/api/history/purge")
async def purge_history(body: dict[str, Any]):
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)
    mapping_id = body.get("mapping_id", "")
    keep_count = body.get("keep_count", 0)
    if not mapping_id:
        return JSONResponse({"error": "mapping_id required"}, status_code=400)
    deleted = history_store.purge_mapping(mapping_id, keep_count)
    return {"deleted": deleted, "mapping_id": mapping_id}


@app.get("/api/history/multi")
async def get_multi_history(
    ids: str = "",           # comma-separated mapping IDs; empty = all
    start: str | None = None,
    end: str | None = None,
    limit: int = 2000,
):
    """Fetch history for multiple mapping IDs in one request.
    Returns: { series: { mapping_id: [{timestamp, value}] }, label_map: {id: label} }
    Used by the Trending page to avoid N parallel individual requests.

    NOTE: This route MUST be declared before /api/history/{mapping_id} so FastAPI
    does not greedily match 'multi' as a mapping_id path parameter.
    """
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)

    mapping_ids = [x.strip() for x in ids.split(",") if x.strip()] if ids else None
    rows = history_store.export_range(
        mapping_ids=mapping_ids,
        start=start,
        end=end,
        limit=min(limit, 10_000),
    )

    # Group by mapping_id → {id: [{timestamp, value}]}
    series: dict[str, list] = {}
    for row in rows:
        mid = row["mapping_id"]
        if mid not in series:
            series[mid] = []
        series[mid].append({"timestamp": row["timestamp"], "value": row["value"]})

    # Build label map for display
    label_map = {
        m.id: (m.label or f"{m.object_type}:{m.object_instance}")
        for m in config_manager.mappings
    }

    return {
        "series": series,
        "label_map": label_map,
        "total_rows": len(rows),
        "mapping_count": len(series),
    }


@app.get("/api/history/{mapping_id}")
async def get_history(mapping_id: str, start: str = None, end: str = None, limit: int = 500):
    if not history_store:
        return JSONResponse({"error": "History store not initialised"}, status_code=503)
    data = history_store.query(mapping_id, start=start, end=end, limit=min(limit, 5000))
    return {"mapping_id": mapping_id, "count": len(data), "records": data}


# ═══════════════════════════════════════════════
# REST API — Config import / export
# ═══════════════════════════════════════════════
@app.get("/api/config/export")
async def export_config():
    return config_manager.export_config()


@app.post("/api/config/import")
async def import_config(data: dict[str, Any]):
    config_manager.import_config(data)
    return {"status": "imported"}


# ═══════════════════════════════════════════════
# WebSocket — real-time data stream
# ═══════════════════════════════════════════════
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive; handle client messages if needed
            data = await ws.receive_text()
            # Could handle commands from the UI here
            logger.debug("WS received: %s", data)
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
    except Exception:
        await ws_manager.disconnect(ws)


# ═══════════════════════════════════════════════
# Logs endpoint (returns last N log lines)
# ═══════════════════════════════════════════════
_log_buffer: list[str] = []
_MAX_LOG_LINES = 500


class BufferHandler(logging.Handler):
    """In-memory ring-buffer log handler for the web UI."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        _log_buffer.append(msg)
        if len(_log_buffer) > _MAX_LOG_LINES:
            _log_buffer.pop(0)


_buf_handler = BufferHandler()
_buf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_buf_handler)


@app.get("/api/logs")
async def get_logs(lines: int = 100):
    return {"logs": _log_buffer[-lines:]}
