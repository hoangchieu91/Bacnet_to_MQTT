"""Modbus RTU Tools Dashboard — FastAPI server

Endpoints:
  GET  /                     → serve index.html
  GET  /api/sniffer/report   → full bus health report
  GET  /api/sniffer/frames   → rolling frame log
  GET  /api/sniffer/pathologies → detected issues
  GET  /api/serial-ports     → available serial ports
  PUT  /api/config           → update config.yaml
  WS   /ws                   → realtime events

  Dual-Pi Correlator (Mode 3):
   GET  /api/correlator/report → cross-correlation report
   GET  /api/correlator/status → Pi-2 connection status
   PUT  /api/correlator/config → update Pi-2 URL
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
from modbus_correlator import DualPiCorrelator
from report_exporter import ReportExporter

logger = logging.getLogger(__name__)

app = FastAPI(title="Modbus RTU Tools Dashboard", version="1.2.0")

_sniffer: ModbusSniffer | None = None
_correlator: DualPiCorrelator | None = None
_exporter: ReportExporter | None = None
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
    global _sniffer, _correlator
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

    # ── Dual-Pi Correlator ─────────────────────────────────────────────────
    dual_cfg = cfg.get("dual_pi", {})
    if dual_cfg.get("enabled", False):
        try:
            _correlator = DualPiCorrelator(
                pi2_url=dual_cfg.get("pi2_url", "http://10.25.7.22:8766"),
                poll_interval=dual_cfg.get("poll_interval_s", 5.0),
                correlation_window_ms=dual_cfg.get("correlation_window_ms", 50.0),
            )
            _get_report = _sniffer.get_report if _sniffer else None
            await _correlator.start(get_local_report=_get_report)
            logger.info("[Dashboard] Dual-Pi Correlator started → %s",
                        dual_cfg.get("pi2_url"))
        except Exception as exc:
            logger.error("[Dashboard] Correlator failed to start: %s", exc)
            _correlator = None

    # ── Report Exporter (offline mode) ────────────────────────────────────
    export_cfg = cfg.get("export", {})
    if export_cfg.get("enabled", False) and _sniffer:
        try:
            _exporter = ReportExporter(
                get_report=_sniffer.get_report,
                export_dir=export_cfg.get("directory", "/data/modbus_reports"),
                interval_s=export_cfg.get("interval_s", 300),
                max_files=export_cfg.get("max_files", 500),
                pi_label=export_cfg.get("pi_label", ""),
            )
            await _exporter.start()
            logger.info("[Dashboard] Report Exporter started → %s",
                        export_cfg.get("directory"))
        except Exception as exc:
            logger.error("[Dashboard] Exporter failed to start: %s", exc)
            _exporter = None

    logger.info("[Dashboard] Startup complete")


@app.on_event("shutdown")
async def shutdown() -> None:
    if _exporter:
        await _exporter.stop()
    if _correlator:
        await _correlator.stop()
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


@app.post("/api/decode")
async def decode_frame(body: dict) -> dict:
    """Decode raw hex bytes into register values — Modbus Poll compatible.

    Supports: UINT16, INT16, UINT32, INT32, Float32, Float32 Inv,
              Double Inv 32, Float64, with 4 byte order modes.

    Input: {"hex": "0103040001006484A3", "byte_order": "AB_CD"}
    """
    import struct as _struct

    raw_hex = body.get("hex", "")
    byte_order = body.get("byte_order", "AB_CD")

    try:
        raw = bytes.fromhex(raw_hex.replace(" ", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string")

    if len(raw) < 3:
        return {"error": "Too short", "registers": []}

    slave_id = raw[0]
    fc = raw[1]
    is_response = fc in (0x03, 0x04) and len(raw) > 3

    if is_response:
        byte_count = raw[2]
        reg_data = raw[3:3+byte_count]
    else:
        reg_data = raw[2:]

    # Parse into 16-bit registers
    registers = []
    for i in range(0, len(reg_data) - 1, 2):
        hi, lo = reg_data[i], reg_data[i+1]
        registers.append((hi << 8) | lo)

    # ── Helper: apply byte order to 2 registers → 32-bit integer ──
    def _make_u32(r0, r1, order):
        if order == "AB_CD":
            return (r0 << 16) | r1
        elif order == "CD_AB":
            return (r1 << 16) | r0
        elif order == "BA_DC":
            return (((r0 & 0xFF) << 8 | (r0 >> 8)) << 16) | \
                   ((r1 & 0xFF) << 8 | (r1 >> 8))
        elif order == "DC_BA":
            return (((r1 & 0xFF) << 8 | (r1 >> 8)) << 16) | \
                   ((r0 & 0xFF) << 8 | (r0 >> 8))
        return (r0 << 16) | r1

    def _to_float32(u32):
        try:
            return round(_struct.unpack(">f", _struct.pack(">I", u32))[0], 6)
        except Exception:
            return None

    def _to_float64(u64):
        try:
            return round(_struct.unpack(">d", _struct.pack(">Q", u64))[0], 10)
        except Exception:
            return None

    # ── Build multi-format decode table ───────────────────────────────────
    result = []
    for idx, val in enumerate(registers):
        entry = {
            "reg": idx,
            "raw_hex": f"{val:04X}",
            "uint16": val,
            "int16": val if val < 0x8000 else val - 0x10000,
            "binary": f"{val:016b}",
        }

        # ── 32-bit values (2 consecutive registers) ──
        if idx < len(registers) - 1:
            r0, r1 = registers[idx], registers[idx+1]

            u32 = _make_u32(r0, r1, byte_order)
            entry["uint32"] = u32
            entry["int32"] = u32 if u32 < 0x80000000 else u32 - 0x100000000

            # Float32 (normal byte order)
            entry["float32"] = _to_float32(u32)

            # Float32 Inverted (word-swapped: swap r0/r1 then decode)
            u32_inv = _make_u32(r1, r0, byte_order)
            entry["float32_inv"] = _to_float32(u32_inv)

            # Double Inverted 32 (byte-swapped within each word)
            r0_bs = ((r0 & 0xFF) << 8) | (r0 >> 8)
            r1_bs = ((r1 & 0xFF) << 8) | (r1 >> 8)
            u32_dinv = _make_u32(r0_bs, r1_bs, byte_order)
            entry["double_inv32"] = _to_float32(u32_dinv)

        # ── 64-bit values / Float64 (4 consecutive registers) ──
        if idx < len(registers) - 3:
            r0, r1, r2, r3 = registers[idx:idx+4]
            if byte_order == "AB_CD":
                u64 = (r0 << 48) | (r1 << 32) | (r2 << 16) | r3
            elif byte_order == "CD_AB":
                u64 = (r3 << 48) | (r2 << 32) | (r1 << 16) | r0
            elif byte_order == "BA_DC":
                def bs(r): return ((r & 0xFF) << 8) | (r >> 8)
                u64 = (bs(r0) << 48) | (bs(r1) << 32) | (bs(r2) << 16) | bs(r3)
            elif byte_order == "DC_BA":
                def bs(r): return ((r & 0xFF) << 8) | (r >> 8)
                u64 = (bs(r3) << 48) | (bs(r2) << 32) | (bs(r1) << 16) | bs(r0)
            else:
                u64 = (r0 << 48) | (r1 << 32) | (r2 << 16) | r3

            entry["uint64"] = u64
            entry["int64"] = u64 if u64 < 0x8000000000000000 else u64 - 0x10000000000000000
            entry["float64"] = _to_float64(u64)

        result.append(entry)

    return {
        "slave_id": slave_id,
        "function_code": fc,
        "byte_count": len(reg_data),
        "register_count": len(registers),
        "byte_order": byte_order,
        "registers": result,
    }


# ── Dual-Pi Correlator API ────────────────────────────────────────────────────

@app.get("/api/correlator/status")
async def correlator_status() -> dict:
    if not _correlator:
        return {"enabled": False}
    return _correlator.get_status()


@app.get("/api/correlator/report")
async def correlator_report() -> dict:
    if not _correlator:
        return {"available": False, "reason": "Correlator not enabled"}
    return _correlator.get_correlation_report()


@app.put("/api/correlator/config")
async def update_correlator_config(body: dict) -> dict:
    global _correlator
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("dual_pi", {}).update(body)
        with open("config.yaml", "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        # Hot-reload correlator if URL changed
        if _correlator and body.get("pi2_url"):
            await _correlator.stop()
            _correlator = DualPiCorrelator(
                pi2_url=body.get("pi2_url", _correlator.pi2_url),
                poll_interval=body.get("poll_interval_s", _correlator.poll_interval),
            )
            _get_report = _sniffer.get_report if _sniffer else None
            await _correlator.start(get_local_report=_get_report)

        return {"status": "saved", "config": cfg.get("dual_pi", {})}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Serial port detection ─────────────────────────────────────────────────────

@app.get("/api/export/status")
async def export_status() -> dict:
    if not _exporter:
        return {"enabled": False}
    return _exporter.get_status()


@app.get("/api/export/list")
async def export_list(limit: int = 50) -> list:
    if not _exporter:
        return []
    return _exporter.list_exports(limit)


@app.post("/api/export/now")
async def export_now() -> dict:
    if not _exporter:
        raise HTTPException(status_code=400, detail="Exporter not enabled")
    return _exporter.export_now()


# ── Serial port detection (original) ─────────────────────────────────────────

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
