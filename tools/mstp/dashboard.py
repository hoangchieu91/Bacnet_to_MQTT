"""MS/TP Tools Dashboard — FastAPI server

Endpoints:
  GET  /                            → serve index.html
  GET  /api/nodes                   → all discovered nodes + status
  GET  /api/nodes/{node_id}         → node detail
  GET  /api/events                  → event log
  GET  /api/stats                   → bus health summary
  POST /api/scan                    → trigger immediate re-scan
  GET  /api/bridge/values           → all cached bridge values
  GET  /api/bridge/{id}/{type}/{i}  → single point value
  WS   /ws                          → realtime node events
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import collections
import time
import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from health_monitor import HealthMonitor
from bridge import MstpBridge
from mstp_sniffer import MstpSniffer, Pathology
from point_discovery import DiscoveryRunner
from bacnet_commander import CommandRunner

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="MS/TP Tools Dashboard", version="1.2.0")

_monitor: HealthMonitor | None = None
_bridge:  MstpBridge | None   = None
_sniffer: MstpSniffer | None  = None
_clients: set[WebSocket]       = set()
_pathology_history: collections.deque = collections.deque(maxlen=2000)
_discovery: DiscoveryRunner    = DiscoveryRunner()
_commander: CommandRunner      = CommandRunner()

STATIC_DIR = Path(__file__).parent / "static"


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def _broadcast(event: dict) -> None:
    dead: set[WebSocket] = set()
    msg = json.dumps(event)
    for ws in list(_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global _monitor, _bridge, _sniffer
    cfg_path = "config.yaml"

    with open(cfg_path) as f:
        import yaml
        cfg = yaml.safe_load(f)

    # Try to start HealthMonitor (needs BAC0 MS/TP) — not critical
    try:
        _monitor = HealthMonitor.from_config(cfg_path, broadcast_cb=_broadcast)
        await _monitor.start()
        _bridge = MstpBridge.from_config(cfg_path, bacnet=_monitor._scanner._bacnet)
        await _bridge.start()
        asyncio.create_task(_monitor.run())
        asyncio.create_task(_bridge.run_poll_loop())
        logger.info("[Dashboard] Scanner + Bridge started")
    except Exception as exc:
        logger.warning("[Dashboard] Scanner/Bridge unavailable: %s — running sniffer-only mode", exc)
        _monitor = None
        _bridge = None

    # Sniffer runs on SAME serial port in passive mode — no BAC0 needed
    sniffer_cfg = cfg.get("sniffer", {})
    if sniffer_cfg.get("enabled", True):
        def _on_pathology(p: Pathology) -> None:
            entry = {
                "type": "pathology",
                "severity": p.severity,
                "code": p.code,
                "description": p.description,
                "nodes_involved": p.nodes_involved,
                "ts": time.time(),
            }
            _pathology_history.append(entry)
            asyncio.create_task(_broadcast(entry))
        def _on_conversation(conv: dict) -> None:
            asyncio.create_task(_broadcast({
                "type": "conversation",
                **conv,
            }))
        try:
            _sniffer = MstpSniffer.from_config(cfg_path, on_pathology=_on_pathology)
            _sniffer.analyzer._on_conversation = _on_conversation
            await _sniffer.start()
            logger.info("[Dashboard] Sniffer started on serial port (PASSIVE)")
        except Exception as exc:
            logger.error("[Dashboard] Sniffer failed to start: %s", exc)
            _sniffer = None

    logger.info("[Dashboard] Startup complete")


@app.on_event("shutdown")
async def shutdown() -> None:
    if _sniffer:
        await _sniffer.stop()
    if _bridge:
        await _bridge.stop()
    if _monitor:
        await _monitor.stop()


# ── Static files ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/api/nodes")
async def get_nodes() -> list[dict]:
    # Primary: HealthMonitor data (BAC0/scanner)
    if _monitor:
        return _monitor.store.get_snapshots()
    # Fallback: sniffer passive data → populate node grid
    if _sniffer:
        return _sniffer_nodes_as_grid()
    return []


# Known BACnet vendor IDs (common in BMS)
VENDOR_NAMES: dict[int, str] = {
    5: "Johnson Controls (JCI)",
    7: "Automated Logic (ALC)",
    8: "Delta Controls",
    15: "TAC/Schneider",
    17: "Honeywell",
    24: "Alerton",
    36: "Tridium",
    95: "Reliable Controls",
    115: "Cylon Controls",
    149: "ABI",
    169: "Distech Controls",
    260: "KMC Controls",
    343: "EasyIO",
    365: "Loytec",
    389: "Contemporary Controls",
    400: "Sauter",
    453: "Phoenix Controls",
    555: "Beckhoff",
    624: "Intesis (HMS)",
    800: "Danfoss",
}

SEG_NAMES = {0: "Both", 1: "Transmit", 2: "Receive", 3: "None"}


def _sniffer_nodes_as_grid() -> list[dict]:
    """Convert sniffer analyzer node stats into the same shape expected by grid UI."""
    if not _sniffer:
        return []
    report = _sniffer.get_report()
    nodes = []
    for ns in report.get("nodes", []):
        mac = ns["mac"]
        fps = ns.get("frames_per_s", 0)
        device_ids = ns.get("device_ids", [])
        vendor_id = ns.get("vendor_id")
        vendor_name = VENDOR_NAMES.get(vendor_id, f"Vendor {vendor_id}") if vendor_id else "—"
        dev_label = f"Device {device_ids[0]}" if device_ids else ""
        nodes.append({
            "address": mac,
            "online": ns.get("online", True),
            "name": f"Node {mac}" + (f" ({dev_label})" if dev_label else ""),
            "vendor": vendor_name,
            "vendor_id": vendor_id,
            "model": "—",
            "rtt_ms": round(ns.get("token_avg_ms") or 0, 1),
            "frames_per_s": fps,
            "bad_crc": ns.get("bad_crc", 0),
            "bad_crc_pct": ns.get("bad_crc_pct", 0),
            "device_ids": device_ids,
            "max_apdu": ns.get("max_apdu"),
            "segmentation": SEG_NAMES.get(ns.get("segmentation"), "—"),
            "total_frames": ns.get("total_frames", 0),
            "bytes_per_s": ns.get("bytes_per_s", 0),
            "token_passes": ns.get("token_passes", 0),
            "token_avg_ms": ns.get("token_avg_ms"),
            "token_max_ms": ns.get("token_max_ms"),
            "frame_types": ns.get("frame_types", {}),
            "responsiveness": ns.get("responsiveness"),
            "unanswered_streak": ns.get("unanswered_streak", 0),
            "total_requests_to": ns.get("total_requests_to", 0),
            "total_replies_from": ns.get("total_replies_from", 0),
            "source": "sniffer",
        })
    return nodes


@app.get("/api/nodes/{node_id}")
async def get_node(node_id: int) -> dict:
    # Primary: HealthMonitor
    if _monitor:
        snapshots = {s["address"]: s for s in _monitor.store.get_snapshots()}
        node = snapshots.get(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        events = _monitor.store.get_events(limit=50, node_id=node_id)
        return {**node, "events": events}
    # Fallback: sniffer
    if _sniffer:
        nodes = {n["address"]: n for n in _sniffer_nodes_as_grid()}
        node = nodes.get(node_id)
        if node:
            return node
    raise HTTPException(status_code=404, detail=f"Node {node_id} not found")


@app.get("/api/events")
async def get_events(limit: int = 100, node_id: int | None = None) -> list[dict]:
    if not _monitor:
        return []
    return _monitor.store.get_events(limit=limit, node_id=node_id)


@app.get("/api/conversations")
async def get_conversations(limit: int = 200, node: int | None = None) -> list[dict]:
    """Return decoded BACnet APDU conversations from sniffer."""
    if not _sniffer:
        return []
    convos = _sniffer.get_conversations(limit=limit)
    if node is not None:
        convos = [c for c in convos if c.get("src") == node or c.get("dst") == node]
    return convos


@app.get("/api/stats")
async def get_stats() -> dict:
    if not _monitor:
        return {}
    stats = _monitor.store.get_stats()
    stats["scan_count"] = _monitor.scan_count
    return stats


@app.get("/api/pathologies")
async def get_pathologies() -> list[dict]:
    """Return recent pathology events (server-side history, survives page reload)."""
    return list(_pathology_history)


@app.post("/api/capture/start")
async def start_capture() -> dict:
    if not _sniffer:
        raise HTTPException(status_code=503, detail="Sniffer offline")
    _sniffer.start_capture("/tmp/mstp_capture.pcap")
    return {"status": "started"}


@app.post("/api/capture/stop")
async def stop_capture() -> dict:
    if not _sniffer:
        raise HTTPException(status_code=503, detail="Sniffer offline")
    info = _sniffer.stop_capture()
    return info


@app.get("/api/capture/download")
async def download_capture() -> FileResponse:
    path = Path("/tmp/mstp_capture.pcap")
    if not path.exists():
        raise HTTPException(status_code=404, detail="No capture file found")
    return FileResponse(
        path,
        media_type="application/vnd.tcpdump.pcap",
        filename=f"mstp_capture_{int(time.time())}.pcap"
    )


# ── Discovery API ─────────────────────────────────────────────────────────────

@app.post("/api/discover/{mac}")
async def start_discovery(mac: int) -> dict:
    """Start auto-discovery of a target MAC's Object List.
    Pauses sniffer, runs MstpMaster in thread, resumes sniffer when done."""
    if _discovery.is_running:
        raise HTTPException(status_code=409, detail="Discovery already running")

    # Read serial config
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    serial_cfg = cfg.get("serial", {})
    port = serial_cfg.get("port", "/dev/ttyUSB0")
    baud = serial_cfg.get("baudrate", 38400)
    my_mac = serial_cfg.get("mac", 127)

    # Look up known device_instance + compute best my_mac from sniffer data
    known_dev = None
    if _sniffer and _sniffer.analyzer:
        node = _sniffer.analyzer._nodes.get(mac)
        if node and node.device_instances:
            known_dev = next(iter(node.device_instances))
            logger.info("[Discovery] Known device instance %d for MAC %d from sniffer", known_dev, mac)
        # Auto-compute my_mac: use (max_active_mac + 1) so ring node will PFM to us
        active_macs = list(_sniffer.analyzer._nodes.keys())
        if active_macs:
            max_mac = max(active_macs)
            candidate = max_mac + 1
            # Make sure candidate doesn't collide and is <= 127
            while candidate in active_macs and candidate <= 126:
                candidate += 1
            if candidate <= 126:
                my_mac = candidate
                logger.info("[Discovery] Auto-selected my_mac=%d (max active=%d)", my_mac, max_mac)

    # Pause sniffer to release serial port
    sniffer_was_running = False
    if _sniffer and _sniffer._running:
        sniffer_was_running = True
        await _sniffer.stop()
        logger.info("[Discovery] Sniffer paused for discovery")
        await asyncio.sleep(1.0)  # Let serial port settle fully

    # Start discovery in thread
    _discovery.start(port, baud, mac, my_mac=my_mac, duration=180.0,
                     known_device_instance=known_dev)

    # Monitor thread and resume sniffer when done
    async def _wait_and_resume():
        while _discovery.is_running:
            # Broadcast progress
            if _discovery.result:
                await _broadcast({
                    "type": "discovery_progress",
                    "mac": mac,
                    "progress": _discovery.result.progress,
                    "status": _discovery.result.status,
                    "object_count": _discovery.result.object_count,
                })
            await asyncio.sleep(1)

        # Resume sniffer
        if sniffer_was_running and _sniffer:
            try:
                await _sniffer.start()
                logger.info("[Discovery] Sniffer resumed")
            except Exception as exc:
                logger.error("[Discovery] Failed to resume sniffer: %s", exc)

        # Broadcast final result
        if _discovery.result:
            await _broadcast({
                "type": "discovery_done",
                "mac": mac,
                "result": _discovery.result.to_dict(),
            })

    asyncio.create_task(_wait_and_resume())
    return {"status": "started", "mac": mac}


@app.get("/api/discover/status")
async def discovery_status() -> dict:
    """Get current discovery status and results."""
    if _discovery.result:
        return _discovery.result.to_dict()
    return {"status": "idle"}


@app.get("/api/discover/csv")
async def discovery_csv() -> Any:
    """Download discovery results as CSV."""
    from fastapi.responses import Response
    if not _discovery.result or not _discovery.result.points:
        raise HTTPException(status_code=404, detail="No discovery data")
    csv_data = _discovery.result.to_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=io_mapping_mac{_discovery.result.mac}.csv"}
    )


@app.post("/api/scan")
async def trigger_scan() -> dict:
    if not _monitor:
        raise HTTPException(status_code=503, detail="Scanner unavailable — running in sniffer-only mode")
    asyncio.create_task(_monitor._scan_cycle())
    return {"status": "scan triggered"}


# ── Commander API ─────────────────────────────────────────────────────────────

@app.post("/api/command")
async def send_command(body: dict) -> dict:
    """Send a BACnet command (read/write/reinit) to a target device.
    Body: {command, target_mac, obj_type, obj_instance, prop_id, value, priority, device_instance, reinit_state, password}
    """
    if _commander.is_running:
        raise HTTPException(status_code=409, detail="Command already running")

    cmd = body.get("command", "read")
    target_mac = body.get("target_mac")
    if target_mac is None:
        raise HTTPException(status_code=400, detail="target_mac required")

    # Read serial config
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    serial_cfg = cfg.get("serial", {})
    port = serial_cfg.get("port", "/dev/ttyUSB0")
    baud = serial_cfg.get("baudrate", 38400)
    my_mac = serial_cfg.get("mac", 127)

    # Pause sniffer
    sniffer_was_running = False
    if _sniffer and _sniffer._running:
        sniffer_was_running = True
        await _sniffer.stop()
        await asyncio.sleep(0.5)

    # Start command in thread
    kwargs = {k: v for k, v in body.items() if k not in ('command', 'target_mac')}
    # Convert value types
    if 'value' in kwargs:
        v = kwargs['value']
        try:
            if '.' in str(v): kwargs['value'] = float(v)
            else: kwargs['value'] = int(v)
        except (ValueError, TypeError):
            pass

    _commander.start(port, baud, target_mac, cmd, my_mac=my_mac, **kwargs)

    # Wait and resume sniffer
    async def _wait():
        while _commander.is_running:
            await asyncio.sleep(0.5)
        if sniffer_was_running and _sniffer:
            try:
                await _sniffer.start()
            except Exception as exc:
                logger.error("[Commander] Failed to resume sniffer: %s", exc)
        if _commander.result:
            await _broadcast({"type": "command_result", **_commander.result})

    asyncio.create_task(_wait())
    return {"status": "started", "command": cmd, "target_mac": target_mac}


@app.get("/api/command/status")
async def command_status() -> dict:
    if _commander.result:
        return _commander.result
    if _commander.is_running:
        return {"status": "running"}
    return {"status": "idle"}


@app.get("/api/mode")
async def get_mode() -> dict:
    """Return current operating mode so UI can adapt."""
    return {
        "scanner_active": _monitor is not None,
        "sniffer_active": _sniffer is not None and _sniffer._running,
        "bridge_active": _bridge is not None,
        "mode": "full" if _monitor else ("sniffer" if _sniffer else "offline"),
    }


@app.get("/api/bridge/values")
async def bridge_values() -> list[dict]:
    if not _bridge:
        return []
    return _bridge.get_all_values()


@app.get("/api/bridge/{node_id}/{obj_type}/{instance}")
async def bridge_point(node_id: int, obj_type: str, instance: int) -> dict:
    if not _bridge:
        raise HTTPException(status_code=503)
    pv = _bridge.get_value(node_id, obj_type, instance)
    if not pv:
        raise HTTPException(status_code=404)
    return pv.to_dict()


# ── Topology API ──────────────────────────────────────────────────────────────

@app.get("/api/topology")
async def get_topology() -> dict:
    """Return token ring topology and traffic matrix for visualization."""
    if not _sniffer:
        return {"nodes": [], "token_ring": [], "traffic": []}
    return _sniffer.analyzer.get_topology()


@app.get("/api/timing")
async def get_timing() -> dict:
    """Return per-node timing analysis: reply delays, jitter, histogram data."""
    if not _sniffer:
        return {"nodes": [], "alerts": []}
    return _sniffer.analyzer.get_timing_report()


# ── Sniffer API ───────────────────────────────────────────────────────────────

@app.get("/api/sniffer/report")
async def sniffer_report() -> dict:
    """Full health report: bus utilization, per-node stats, pathologies."""
    if not _sniffer:
        return {"enabled": False}
    return _sniffer.get_report()


@app.get("/api/sniffer/frames")
async def sniffer_frames(limit: int = 100) -> list[dict]:
    """Rolling log of the last N raw frames captured."""
    if not _sniffer:
        return []
    return _sniffer.get_frame_log(limit=limit)


@app.get("/api/sniffer/pathologies")
async def sniffer_pathologies() -> list[dict]:
    """Current list of detected bus pathologies."""
    if not _sniffer:
        return []
    report = _sniffer.get_report()
    return report.get("pathologies", [])


# ── File Transfer API ─────────────────────────────────────────────────────────

from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse
from bacnet_file_transfer import BacnetFileTransfer, TransferProgress

@app.post("/api/file/upload")
async def file_upload(
    file: UploadFile = File(...),
    address: str = Form(...),
    file_instance: int = Form(1),
    reload: str = Form("false"),
    device_instance: int = Form(0),
) -> dict:
    """Receive file from browser, upload to BACnet device via AtomicWriteFile."""
    contents = await file.read()
    bacnet = _monitor._scanner._bacnet if _monitor else None

    prog_state: list[TransferProgress] = []

    def _cb(p: TransferProgress) -> None:
        prog_state.clear()
        prog_state.append(p)
        # Broadcast progress to WebSocket clients
        asyncio.create_task(_broadcast({
            "type": "xfer_progress",
            "file": file.filename,
            "address": address,
            "pct": round(p.pct, 1),
            "status": p.status,
        }))

    xfer = BacnetFileTransfer.from_config("config.yaml", bacnet=bacnet, progress_cb=_cb)
    result = await xfer.upload(
        address=address,
        file_path="/dev/stdin",   # handled below via temp file
        file_object_instance=file_instance,
    )
    # Re-do with actual bytes via helper
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename or "upload").suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = await xfer.upload(address, tmp_path, file_instance)
        if result.status == "done" and reload.lower() == "true" and device_instance:
            await xfer.trigger_reload(address, device_instance)
    finally:
        os.unlink(tmp_path)
        if xfer._owned_bacnet:
            await xfer.close()

    return result.to_dict()


@app.get("/api/file/download")
async def file_download(
    address: str,
    file_instance: int = 1,
    filename: str = "download.app",
) -> StreamingResponse:
    """Download File Object from BACnet device and stream to browser."""
    import tempfile, os
    bacnet = _monitor._scanner._bacnet if _monitor else None
    xfer = BacnetFileTransfer.from_config("config.yaml", bacnet=bacnet)

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        tmp_path = tmp.name

    result = await xfer.download(address, file_instance, tmp_path)
    if xfer._owned_bacnet:
        await xfer.close()

    if result.status != "done":
        os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=result.error)

    def iterfile():
        with open(tmp_path, "rb") as f:
            yield from f
        os.unlink(tmp_path)

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Serial port detection ─────────────────────────────────────────────────────

@app.get("/api/serial-ports")
async def list_serial_ports() -> dict:
    """List available serial ports on the system."""
    import glob
    ports = []
    for pattern in ["/dev/ttyUSB*", "/dev/ttyAMA*", "/dev/ttyS[0-9]"]:
        for p in sorted(glob.glob(pattern)):
            ports.append({"port": p, "description": _guess_port_desc(p)})
    return {"ports": ports}


def _guess_port_desc(port: str) -> str:
    name = Path(port).name
    if "USB" in name:
        return "USB-Serial (CH340/CP210x/FTDI)"
    if "AMA" in name:
        return "Raspberry Pi UART (GPIO14/15)"
    return "Serial port"


# ── Runtime config API ────────────────────────────────────────────────────────

@app.put("/api/config")
async def update_config(body: dict) -> dict:
    """Update config.yaml and schedule restart of scanner+sniffer."""
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)

        if "serial" in body:
            cfg.setdefault("serial", {}).update(body["serial"])
        if "scan" in body:
            cfg.setdefault("scan", {}).update(body["scan"])

        with open("config.yaml", "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        logger.info("[Config] Updated: %s", body)
        return {"status": "saved", "config": cfg}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        # Send initial state
        if _monitor:
            nodes = _monitor.store.get_snapshots()
            stats = _monitor.store.get_stats()
            await ws.send_text(json.dumps({"type": "init", "nodes": nodes, "stats": stats}))
        while True:
            await ws.receive_text()   # keep-alive — client can send pings
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="MS/TP Tools Dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host",   default=None)
    parser.add_argument("--port",   type=int, default=None)
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    api_cfg = cfg.get("api", {})
    host = args.host or api_cfg.get("host", "0.0.0.0")
    port = args.port or api_cfg.get("port", 8765)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    uvicorn.run(
        "dashboard:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
