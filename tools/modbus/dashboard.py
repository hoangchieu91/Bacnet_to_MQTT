"""Modbus RTU Tools Dashboard — FastAPI server

Endpoints:
  GET  /                     → serve index.html
  GET  /api/sniffer/report   → full bus health report
  GET  /api/sniffer/frames   → rolling frame log
  GET  /api/sniffer/pathologies → detected issues
  GET  /api/serial-ports     → available serial ports
  PUT  /api/config           → update config.yaml
  WS   /ws                   → realtime events
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

from modbus_sniffer import ModbusSniffer, Pathology

logger = logging.getLogger(__name__)

app = FastAPI(title="Modbus RTU Tools Dashboard", version="1.0.0")

_sniffer: ModbusSniffer | None = None
_clients: set[WebSocket] = set()
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
    global _sniffer
    cfg_path = "config.yaml"

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    sniffer_cfg = cfg.get("sniffer", {})
    if sniffer_cfg.get("enabled", True):
        def _on_pathology(p: Pathology) -> None:
            asyncio.create_task(_broadcast({
                "type": "pathology",
                "severity": p.severity,
                "code": p.code,
                "description": p.description,
                "slaves_involved": p.slaves_involved,
            }))
        try:
            _sniffer = ModbusSniffer.from_config(cfg_path, on_pathology=_on_pathology)
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


# ── Static files ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── Sniffer API ───────────────────────────────────────────────────────────────

@app.get("/api/sniffer/report")
async def sniffer_report() -> dict:
    if not _sniffer:
        return {"enabled": False}
    return _sniffer.get_report()


@app.get("/api/sniffer/frames")
async def sniffer_frames(limit: int = 100) -> list[dict]:
    if not _sniffer:
        return []
    return _sniffer.get_frame_log(limit=limit)


@app.get("/api/sniffer/pathologies")
async def sniffer_pathologies() -> list[dict]:
    if not _sniffer:
        return []
    report = _sniffer.get_report()
    return report.get("pathologies", [])


# ── Serial port detection ─────────────────────────────────────────────────────

@app.get("/api/serial-ports")
async def list_serial_ports() -> dict:
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


# ── Runtime config ────────────────────────────────────────────────────────────

@app.put("/api/config")
async def update_config(body: dict) -> dict:
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        if "serial" in body:
            cfg.setdefault("serial", {}).update(body["serial"])
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
        if _sniffer:
            report = _sniffer.get_report()
            await ws.send_text(json.dumps({"type": "init", "report": report}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Modbus RTU Tools Dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    api_cfg = cfg.get("api", {})
    host = args.host or api_cfg.get("host", "0.0.0.0")
    port = args.port or api_cfg.get("port", 8766)

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
