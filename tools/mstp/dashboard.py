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

import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from health_monitor import HealthMonitor
from bridge import MstpBridge
from mstp_sniffer import MstpSniffer, Pathology

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="MS/TP Tools Dashboard", version="1.0.0")

_monitor: HealthMonitor | None = None
_bridge:  MstpBridge | None   = None
_sniffer: MstpSniffer | None  = None
_clients: set[WebSocket]       = set()

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

    _monitor = HealthMonitor.from_config(cfg_path, broadcast_cb=_broadcast)
    await _monitor.start()

    _bridge = MstpBridge.from_config(cfg_path, bacnet=_monitor._scanner._bacnet)
    await _bridge.start()

    # Sniffer runs on SAME serial port in passive mode — no need to join token ring
    sniffer_cfg = cfg.get("sniffer", {})
    if sniffer_cfg.get("enabled", True):
        def _on_pathology(p: Pathology) -> None:
            asyncio.create_task(_broadcast({
                "type": "pathology",
                "severity": p.severity,
                "code": p.code,
                "description": p.description,
                "nodes_involved": p.nodes_involved,
            }))
        _sniffer = MstpSniffer.from_config(cfg_path, on_pathology=_on_pathology)
        await _sniffer.start()

    # Launch background tasks
    asyncio.create_task(_monitor.run())
    asyncio.create_task(_bridge.run_poll_loop())
    logger.info("[Dashboard] Background tasks started")


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
    if not _monitor:
        return []
    return _monitor.store.get_snapshots()


@app.get("/api/nodes/{node_id}")
async def get_node(node_id: int) -> dict:
    if not _monitor:
        raise HTTPException(status_code=503)
    snapshots = {s["address"]: s for s in _monitor.store.get_snapshots()}
    node = snapshots.get(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    events = _monitor.store.get_events(limit=50, node_id=node_id)
    return {**node, "events": events}


@app.get("/api/events")
async def get_events(limit: int = 100, node_id: int | None = None) -> list[dict]:
    if not _monitor:
        return []
    return _monitor.store.get_events(limit=limit, node_id=node_id)


@app.get("/api/stats")
async def get_stats() -> dict:
    if not _monitor:
        return {}
    stats = _monitor.store.get_stats()
    stats["scan_count"] = _monitor.scan_count
    return stats


@app.post("/api/scan")
async def trigger_scan() -> dict:
    if not _monitor:
        raise HTTPException(status_code=503)
    asyncio.create_task(_monitor._scan_cycle())
    return {"status": "scan triggered"}


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
