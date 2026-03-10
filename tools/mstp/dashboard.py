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

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="MS/TP Tools Dashboard", version="1.0.0")

_monitor: HealthMonitor | None = None
_bridge:  MstpBridge | None   = None
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
    global _monitor, _bridge
    cfg_path = "config.yaml"

    _monitor = HealthMonitor.from_config(cfg_path, broadcast_cb=_broadcast)
    await _monitor.start()

    _bridge = MstpBridge.from_config(cfg_path, bacnet=_monitor._scanner._bacnet)
    await _bridge.start()

    # Launch background tasks
    asyncio.create_task(_monitor.run())
    asyncio.create_task(_bridge.run_poll_loop())
    logger.info("[Dashboard] Background tasks started")


@app.on_event("shutdown")
async def shutdown() -> None:
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
