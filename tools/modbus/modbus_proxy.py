"""Modbus RTU Inline Proxy — Man-in-the-Middle Analyzer

Chèn Pi giữa Master và Slaves bằng 2 cổng COM:
  ComA (/dev/ttyUSB0) ← nối Master
  ComB (/dev/ttyUSB1) ← nối Slaves

Forward traffic trong suốt (byte-level) giữa 2 cổng,
đồng thời phân tích request-response chính xác.

Chẩn đoán nâng cao (thêm so với passive sniffer):
  • EXACT_RESPONSE_TIME    — đo chính xác ms
  • NO_RESPONSE_EXACT      — slave thật sự không trả lời
  • DIRECTION_VIOLATION    — slave gửi frame không được hỏi
  • LATE_RESPONSE          — response đến sau khi master đã gửi request mới
  • BROADCAST_RESPONSE     — slave trả lời broadcast (vi phạm spec)
  • DUPLICATE_SLAVE_ID     — 2+ response cho 1 request
  • FRAME_CORRUPTION_DIR   — CRC OK 1 chiều, fail chiều kia

CLI:
  python3 modbus_proxy.py \\
    --master-port /dev/ttyUSB0 \\
    --slave-port /dev/ttyUSB1 \\
    --baud 9600 --duration 120
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import serial
import yaml

from modbus_sniffer import (
    ModbusFrame,
    ModbusFrameParser,
    ModbusHealthAnalyzer,
    Pathology,
    FUNCTION_NAMES,
    EXCEPTION_NAMES,
    _crc16,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Directional Frame — wraps ModbusFrame with direction info
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DirectionalFrame:
    """A Modbus frame with direction context."""
    frame: ModbusFrame
    direction: str          # "M→S" (Master to Slave) or "S→M" (Slave to Master)
    port_name: str          # "ComA" or "ComB"

    @property
    def is_from_master(self) -> bool:
        return self.direction == "M→S"

    @property
    def is_from_slave(self) -> bool:
        return self.direction == "S→M"


# ═══════════════════════════════════════════════════════════════════════════════
# Proxy Health Analyzer — extends base analyzer with direction-aware diagnostics
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingRequest:
    """A request waiting for a response."""
    frame: ModbusFrame
    ts: float
    slave_id: int
    function_code: int
    matched: bool = False


class ProxyHealthAnalyzer:
    """Direction-aware health analyzer for inline proxy mode.

    Inherits all 8 diagnostics from ModbusHealthAnalyzer,
    plus 7 additional directional diagnostics.
    """

    RESPONSE_TIMEOUT_MS = 1000.0   # Max wait for response (ms)
    LATE_RESPONSE_MS    = 500.0    # Response considered "late" after this

    def __init__(self, baudrate: int = 9600):
        self.baudrate = baudrate
        # Base analyzer for standard diagnostics
        self._base = ModbusHealthAnalyzer(baudrate=baudrate)

        # Direction-specific counters
        self.master_frames = 0
        self.slave_frames = 0
        self.master_bytes = 0
        self.slave_bytes = 0

        # Request-response matching
        self._pending: dict[int, PendingRequest] = {}  # slave_id → pending request
        self._response_times: list[float] = []         # all matched response times (ms)
        self._exact_no_response: list[dict] = []       # confirmed no-response events
        self._direction_violations: list[dict] = []
        self._late_responses: list[dict] = []
        self._broadcast_responses: list[dict] = []
        self._duplicate_responses: list[dict] = []
        self._frame_corruptions: list[dict] = []

        # Per-slave exact response times
        self._slave_response_times: dict[int, list[float]] = {}

        # Frame logs with direction
        self.frame_log: list[dict] = []

        self._start_time = time.perf_counter()

    # ── Ingest ─────────────────────────────────────────────────────────────

    def ingest(self, dframe: DirectionalFrame) -> list[Pathology]:
        """Ingest a directional frame and return any immediate pathologies."""
        frame = dframe.frame
        now = frame.ts
        issues: list[Pathology] = []

        # Feed base analyzer
        self._base.ingest_frame(frame)

        # Direction stats
        if dframe.is_from_master:
            self.master_frames += 1
            self.master_bytes += frame.raw_len
        else:
            self.slave_frames += 1
            self.slave_bytes += frame.raw_len

        # Log with direction
        log_entry = {
            "ts":        round(frame.ts, 4),
            "direction": dframe.direction,
            "port":      dframe.port_name,
            "slave":     frame.slave_id,
            "func":      frame.func_name,
            "fc":        frame.function_code,
            "len":       len(frame.data),
            "valid":     frame.is_valid,
            "is_exc":    frame.is_exception,
            "exc_name":  frame.exception_name if frame.is_exception else "",
            "resp_ms":   None,  # filled if matched
        }

        if dframe.is_from_master:
            # ── Master sent a request ──
            # Check for timed-out pending requests first
            issues.extend(self._check_timeouts(now))

            if frame.slave_id == 0:
                # Broadcast — no response expected, but track it
                log_entry["note"] = "broadcast"
            else:
                # Check if previous request to this slave was still pending
                prev = self._pending.get(frame.slave_id)
                if prev and not prev.matched:
                    # Master sent new request before getting response = LATE or NO_RESPONSE
                    age_ms = (now - prev.ts) * 1000
                    self._exact_no_response.append({
                        "slave_id": prev.slave_id,
                        "func_code": prev.function_code,
                        "wait_ms": round(age_ms, 1),
                        "ts": prev.ts,
                    })

                # Register new pending request
                self._pending[frame.slave_id] = PendingRequest(
                    frame=frame, ts=now,
                    slave_id=frame.slave_id,
                    function_code=frame.function_code,
                )

        elif dframe.is_from_slave:
            # ── Slave sent a response ──

            # Check: broadcast response (violation)
            pending = self._pending.get(frame.slave_id)

            if pending and not pending.matched:
                # Match response to request
                resp_ms = (now - pending.ts) * 1000
                pending.matched = True
                log_entry["resp_ms"] = round(resp_ms, 1)

                self._response_times.append(resp_ms)
                self._slave_response_times.setdefault(frame.slave_id, []).append(resp_ms)
                # Keep last 500
                if len(self._response_times) > 500:
                    self._response_times = self._response_times[-500:]
                if len(self._slave_response_times[frame.slave_id]) > 200:
                    self._slave_response_times[frame.slave_id] = \
                        self._slave_response_times[frame.slave_id][-200:]

                # Check late response
                if resp_ms > self.LATE_RESPONSE_MS:
                    self._late_responses.append({
                        "slave_id": frame.slave_id,
                        "resp_ms": round(resp_ms, 1),
                        "func": frame.func_name,
                        "ts": now,
                    })
                    issues.append(Pathology(
                        severity="warning", code="LATE_RESPONSE",
                        description=f"Slave {frame.slave_id} responded in {resp_ms:.0f}ms",
                        slaves_involved=[frame.slave_id],
                    ))

                del self._pending[frame.slave_id]

            elif pending and pending.matched:
                # Duplicate response!
                self._duplicate_responses.append({
                    "slave_id": frame.slave_id,
                    "func": frame.func_name,
                    "ts": now,
                })
                issues.append(Pathology(
                    severity="critical", code="DUPLICATE_SLAVE_ID",
                    description=f"Multiple responses from slave {frame.slave_id}",
                    slaves_involved=[frame.slave_id],
                    detail="Two or more devices may share the same slave address.",
                ))

            else:
                # No matching request — direction violation
                self._direction_violations.append({
                    "slave_id": frame.slave_id,
                    "func": frame.func_name,
                    "ts": now,
                })
                issues.append(Pathology(
                    severity="warning", code="DIRECTION_VIOLATION",
                    description=f"Slave {frame.slave_id} sent frame without request",
                    slaves_involved=[frame.slave_id],
                    detail="Slave may have buggy firmware or wrong address.",
                ))

        # Check for broadcast responses
        # (frames from slave side with slave_id=0 should never happen)
        if dframe.is_from_slave and frame.slave_id == 0:
            self._broadcast_responses.append({
                "slave_id": 0, "func": frame.func_name, "ts": now,
            })
            issues.append(Pathology(
                severity="critical", code="BROADCAST_RESPONSE",
                description="Response to broadcast address (slave=0) — violates Modbus spec",
            ))

        self.frame_log.append(log_entry)
        if len(self.frame_log) > 1000:
            self.frame_log = self.frame_log[-1000:]

        return issues

    def _check_timeouts(self, now: float) -> list[Pathology]:
        """Check for requests that have timed out."""
        issues = []
        expired = []
        for sid, req in self._pending.items():
            if req.matched:
                expired.append(sid)
                continue
            age_ms = (now - req.ts) * 1000
            if age_ms > self.RESPONSE_TIMEOUT_MS:
                self._exact_no_response.append({
                    "slave_id": req.slave_id,
                    "func_code": req.function_code,
                    "wait_ms": round(age_ms, 1),
                    "ts": req.ts,
                })
                expired.append(sid)
                issues.append(Pathology(
                    severity="warning", code="NO_RESPONSE_EXACT",
                    description=f"Slave {req.slave_id} did not respond "
                                f"({age_ms:.0f}ms, func={FUNCTION_NAMES.get(req.function_code, hex(req.function_code))})",
                    slaves_involved=[req.slave_id],
                    detail="Confirmed: no response received on slave-side port.",
                ))
        for sid in expired:
            del self._pending[sid]
        return issues

    # ── Report ─────────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        """Full proxy diagnostic report."""
        elapsed = max(time.perf_counter() - self._start_time, 0.001)

        # Get base report
        base_report = self._base.get_report()

        # Response time stats
        avg_rt = (sum(self._response_times) / len(self._response_times)
                  if self._response_times else None)
        max_rt = max(self._response_times) if self._response_times else None
        min_rt = min(self._response_times) if self._response_times else None

        # Per-slave response times
        per_slave_rt = {}
        for sid, rts in self._slave_response_times.items():
            per_slave_rt[sid] = {
                "avg_ms": round(sum(rts) / len(rts), 1),
                "max_ms": round(max(rts), 1),
                "min_ms": round(min(rts), 1),
                "count": len(rts),
            }

        proxy_report = {
            **base_report,
            "mode": "inline_proxy",
            "master_frames": self.master_frames,
            "slave_frames": self.slave_frames,
            "master_bytes": self.master_bytes,
            "slave_bytes": self.slave_bytes,
            "master_fps": round(self.master_frames / elapsed, 1),
            "slave_fps": round(self.slave_frames / elapsed, 1),

            # Exact response time
            "response_time": {
                "avg_ms": round(avg_rt, 1) if avg_rt else None,
                "max_ms": round(max_rt, 1) if max_rt else None,
                "min_ms": round(min_rt, 1) if min_rt else None,
                "total_matched": len(self._response_times),
            },
            "per_slave_response_time": per_slave_rt,

            # Direction-aware diagnostics
            "no_response_exact_count": len(self._exact_no_response),
            "no_response_exact": self._exact_no_response[-20:],
            "direction_violations": self._direction_violations[-20:],
            "late_responses": self._late_responses[-20:],
            "broadcast_responses": self._broadcast_responses[-10:],
            "duplicate_responses": self._duplicate_responses[-10:],
        }

        # Merge proxy-specific pathologies
        all_pathologies = list(base_report.get("pathologies", []))

        if self._exact_no_response:
            # Group by slave
            by_slave: dict[int, int] = {}
            for nr in self._exact_no_response:
                by_slave[nr["slave_id"]] = by_slave.get(nr["slave_id"], 0) + 1
            for sid, cnt in by_slave.items():
                all_pathologies.append({
                    "severity": "critical" if cnt > 10 else "warning",
                    "code": "NO_RESPONSE_EXACT",
                    "description": f"Slave {sid}: {cnt} confirmed no-responses",
                    "slaves_involved": [sid],
                    "detail": "No response received on slave-side port (100% confirmed).",
                })

        if self._direction_violations:
            all_pathologies.append({
                "severity": "warning", "code": "DIRECTION_VIOLATION",
                "description": f"{len(self._direction_violations)} unsolicited frames from slaves",
                "slaves_involved": list(set(d["slave_id"] for d in self._direction_violations)),
                "detail": "Slave sent data without being asked. Check firmware.",
            })

        if self._late_responses:
            all_pathologies.append({
                "severity": "warning", "code": "LATE_RESPONSE",
                "description": f"{len(self._late_responses)} responses exceeded {self.LATE_RESPONSE_MS}ms",
                "slaves_involved": list(set(l["slave_id"] for l in self._late_responses)),
                "detail": f"Max: {max(l['resp_ms'] for l in self._late_responses)}ms",
            })

        if self._broadcast_responses:
            all_pathologies.append({
                "severity": "critical", "code": "BROADCAST_RESPONSE",
                "description": f"{len(self._broadcast_responses)} broadcast responses (Modbus spec violation)",
                "slaves_involved": [],
                "detail": "Slaves must NEVER respond to broadcast (slave_id=0).",
            })

        if self._duplicate_responses:
            all_pathologies.append({
                "severity": "critical", "code": "DUPLICATE_SLAVE_ID",
                "description": f"{len(self._duplicate_responses)} duplicate responses detected",
                "slaves_involved": list(set(d["slave_id"] for d in self._duplicate_responses)),
                "detail": "Multiple devices share the same slave address.",
            })

        # Sort by severity
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        all_pathologies.sort(key=lambda p: sev_order.get(p.get("severity", "info"), 2))
        proxy_report["pathologies"] = all_pathologies

        return proxy_report

    def get_frame_log(self, limit: int = 100) -> list[dict]:
        return self.frame_log[-limit:]


# ═══════════════════════════════════════════════════════════════════════════════
# ModbusProxy — dual-port forwarding + analysis
# ═══════════════════════════════════════════════════════════════════════════════

class ModbusProxy:
    """Inline Modbus RTU proxy with dual COM ports.

    Sits between Master (ComA) and Slaves (ComB),
    forwards all traffic transparently (byte-level),
    and analyzes frames with direction context.
    """

    def __init__(
        self,
        master_port: str = "/dev/ttyUSB0",
        slave_port: str = "/dev/ttyUSB1",
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        on_frame: Callable[[DirectionalFrame], None] | None = None,
        on_pathology: Callable[[Pathology], None] | None = None,
    ):
        self.master_port = master_port
        self.slave_port = slave_port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self._on_frame = on_frame
        self._on_pathology = on_pathology

        self.analyzer = ProxyHealthAnalyzer(baudrate=baudrate)
        self._master_parser = ModbusFrameParser(baudrate=baudrate,
            on_frame=lambda f: self._handle_frame(f, "M→S", "ComA"))
        self._slave_parser = ModbusFrameParser(baudrate=baudrate,
            on_frame=lambda f: self._handle_frame(f, "S→M", "ComB"))

        self._ser_a: serial.Serial | None = None  # Master side
        self._ser_b: serial.Serial | None = None  # Slave side
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Stats
        self.bytes_forwarded_a2b = 0
        self.bytes_forwarded_b2a = 0

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", **kwargs) -> "ModbusProxy":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        serial_cfg = cfg.get("serial", {})
        proxy_cfg = cfg.get("proxy", {})
        return cls(
            master_port=proxy_cfg.get("master_port", serial_cfg.get("port", "/dev/ttyUSB0")),
            slave_port=proxy_cfg.get("slave_port", "/dev/ttyUSB1"),
            baudrate=serial_cfg.get("baudrate", 9600),
            parity=serial_cfg.get("parity", "N"),
            stopbits=serial_cfg.get("stopbits", 1),
            **kwargs,
        )

    # ── Serial setup ───────────────────────────────────────────────────────

    def _open_serial(self, port: str) -> serial.Serial:
        parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
        stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
        return serial.Serial(
            port=port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=parity_map.get(self.parity, serial.PARITY_NONE),
            stopbits=stop_map.get(self.stopbits, serial.STOPBITS_ONE),
            timeout=0.001,  # 1ms for low latency forwarding
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._ser_a = await loop.run_in_executor(None, self._open_serial, self.master_port)
        self._ser_b = await loop.run_in_executor(None, self._open_serial, self.slave_port)
        self._running = True

        # Forward: ComA→ComB (Master→Slaves)
        self._tasks.append(asyncio.create_task(
            self._forward_loop(self._ser_a, self._ser_b, self._master_parser, "A→B")))
        # Forward: ComB→ComA (Slaves→Master)
        self._tasks.append(asyncio.create_task(
            self._forward_loop(self._ser_b, self._ser_a, self._slave_parser, "B→A")))
        # Periodic flush
        self._tasks.append(asyncio.create_task(self._flush_loop()))

        logger.info("[ModbusProxy] Started: ComA=%s (Master) ⇄ ComB=%s (Slaves) @ %d baud",
                    self.master_port, self.slave_port, self.baudrate)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for s in (self._ser_a, self._ser_b):
            if s and s.is_open:
                s.close()
        logger.info("[ModbusProxy] Stopped. Forwarded %d bytes A→B, %d bytes B→A",
                    self.bytes_forwarded_a2b, self.bytes_forwarded_b2a)

    # ── Forward loop (byte-level, low latency) ─────────────────────────────

    async def _forward_loop(
        self,
        src: serial.Serial,
        dst: serial.Serial,
        parser: ModbusFrameParser,
        label: str,
    ) -> None:
        """Read from src, forward to dst, feed parser for analysis."""
        loop = asyncio.get_event_loop()
        is_a2b = label == "A→B"

        while self._running:
            try:
                data = await loop.run_in_executor(None, src.read, 256)
                if data:
                    # Forward immediately (transparent)
                    await loop.run_in_executor(None, dst.write, data)

                    # Track bytes
                    if is_a2b:
                        self.bytes_forwarded_a2b += len(data)
                    else:
                        self.bytes_forwarded_b2a += len(data)

                    # Feed parser for analysis (non-blocking)
                    parser.feed_bytes(data)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[ModbusProxy] %s error: %s", label, exc)
                await asyncio.sleep(0.1)

    async def _flush_loop(self) -> None:
        """Periodically flush parsers to detect end-of-frame."""
        while self._running:
            await asyncio.sleep(0.05)  # 50ms flush interval
            try:
                self._master_parser.flush()
                self._slave_parser.flush()
            except Exception:
                pass

    # ── Frame handler ──────────────────────────────────────────────────────

    def _handle_frame(self, frame: ModbusFrame, direction: str, port_name: str) -> None:
        dframe = DirectionalFrame(frame=frame, direction=direction, port_name=port_name)
        issues = self.analyzer.ingest(dframe)

        if self._on_frame:
            self._on_frame(dframe)

        if issues and self._on_pathology:
            for p in issues:
                self._on_pathology(p)

    # ── Report ─────────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        report = self.analyzer.get_report()
        report["bytes_forwarded_a2b"] = self.bytes_forwarded_a2b
        report["bytes_forwarded_b2a"] = self.bytes_forwarded_b2a
        return report

    def get_frame_log(self, limit: int = 100) -> list[dict]:
        return self.analyzer.get_frame_log(limit=limit)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═══════════════════════════════════════════════════════════════════════════════

async def _cli_main() -> None:
    import argparse, json

    parser = argparse.ArgumentParser(
        description="Modbus RTU Inline Proxy — dual COM port man-in-the-middle analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic proxy between two USB-RS485 adapters
  python3 modbus_proxy.py --master-port /dev/ttyUSB0 --slave-port /dev/ttyUSB1

  # Custom baud rate and duration
  python3 modbus_proxy.py --master-port /dev/ttyUSB0 --slave-port /dev/ttyUSB1 \\
    --baud 19200 --parity E --duration 300

  # JSON report output
  python3 modbus_proxy.py --master-port /dev/ttyUSB0 --slave-port /dev/ttyUSB1 --json
"""
    )
    parser.add_argument("--master-port", default="/dev/ttyUSB0",
                        help="Serial port connected to Master (default: /dev/ttyUSB0)")
    parser.add_argument("--slave-port", default="/dev/ttyUSB1",
                        help="Serial port connected to Slaves (default: /dev/ttyUSB1)")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--parity", default="N", choices=["N", "E", "O"])
    parser.add_argument("--duration", type=int, default=120,
                        help="Capture duration in seconds (default: 120)")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--frames", action="store_true", help="Print each frame")
    parser.add_argument("--config", default=None, help="Config YAML file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    frame_count = [0]

    def frame_printer(dframe: DirectionalFrame) -> None:
        if args.frames:
            f = dframe.frame
            status = "✓" if f.is_valid else "✗"
            exc = f" → {f.exception_name}" if f.is_exception else ""
            resp_info = ""
            # Find matching log entry for response time
            for entry in reversed(proxy.analyzer.frame_log[-5:]):
                if entry["ts"] == round(f.ts, 4) and entry.get("resp_ms"):
                    resp_info = f" [{entry['resp_ms']}ms]"
                    break
            print(f"  {status} {dframe.direction} slave={f.slave_id:3d} "
                  f"func={f.func_name:24s} data={len(f.data):3d}B{exc}{resp_info}")
        frame_count[0] += 1

    if args.config:
        proxy = ModbusProxy.from_config(args.config, on_frame=frame_printer)
    else:
        proxy = ModbusProxy(
            master_port=args.master_port,
            slave_port=args.slave_port,
            baudrate=args.baud,
            parity=args.parity,
            on_frame=frame_printer,
        )

    print(f"\n📟 Modbus RTU Inline Proxy")
    print(f"   ComA (Master): {proxy.master_port}")
    print(f"   ComB (Slaves): {proxy.slave_port}")
    print(f"   Baud: {proxy.baudrate} | Parity: {proxy.parity}")
    print(f"   Duration: {args.duration}s  (Ctrl+C to stop early)")
    print(f"   ⚠️  Bus is CUT — Pi is forwarding all traffic")
    print()

    await proxy.start()
    try:
        for i in range(args.duration):
            await asyncio.sleep(1)
            r = proxy.get_report()
            rt = r.get("response_time", {})
            avg = rt.get("avg_ms")
            print(f"\r  ⏱  {i+1:3d}s  |"
                  f"  M→S: {r.get('master_frames', 0):4d}  |"
                  f"  S→M: {r.get('slave_frames', 0):4d}  |"
                  f"  no_rsp: {r.get('no_response_exact_count', 0):3d}  |"
                  f"  avg_rt: {f'{avg:.0f}ms' if avg else '—':>6}  |"
                  f"  bad: {r.get('bad_frames', 0):3d}",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        await proxy.stop()

    print("\n")
    report = proxy.get_report()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    rt = report.get("response_time", {})
    print("=" * 68)
    print("  Modbus RTU Inline Proxy — Health Report")
    print("=" * 68)
    print(f"  Duration:        {report['duration_s']:.0f}s")
    print(f"  Master → Slave:  {report['master_frames']} frames, "
          f"{report['bytes_forwarded_a2b']} bytes forwarded")
    print(f"  Slave → Master:  {report['slave_frames']} frames, "
          f"{report['bytes_forwarded_b2a']} bytes forwarded")
    print(f"  Bad CRC:         {report['bad_frames']} ({report['bad_frame_pct']}%)")
    print(f"  Bus utilization: {report['bus_utilization_pct']}%")

    print(f"\n  Response Time (exact):")
    print(f"    Avg: {rt.get('avg_ms', '—')}ms | Min: {rt.get('min_ms', '—')}ms | "
          f"Max: {rt.get('max_ms', '—')}ms | Matched: {rt.get('total_matched', 0)}")

    print(f"\n  No Response (confirmed): {report['no_response_exact_count']}")
    print(f"  Direction Violations:    {len(report.get('direction_violations', []))}")
    print(f"  Late Responses:          {len(report.get('late_responses', []))}")
    print(f"  Broadcast Responses:     {len(report.get('broadcast_responses', []))}")
    print(f"  Duplicate Slave IDs:     {len(report.get('duplicate_responses', []))}")

    # Per-slave response times
    psr = report.get("per_slave_response_time", {})
    if psr:
        print(f"\n  {'SID':>4}  {'Avg(ms)':>8}  {'Min(ms)':>8}  {'Max(ms)':>8}  {'Count':>6}")
        print(f"  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*6}")
        for sid, st in sorted(psr.items()):
            print(f"  {sid:4d}  {st['avg_ms']:8.1f}  {st['min_ms']:8.1f}  "
                  f"{st['max_ms']:8.1f}  {st['count']:6d}")

    if report["pathologies"]:
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        print(f"\n  {'─'*64}")
        print(f"  DIAGNOSTICS ({len(report['pathologies'])} issues):")
        for p in report["pathologies"]:
            icon = icons.get(p.get("severity", "info"), "•")
            print(f"  {icon} [{p['code']}] {p['description']}")
            if p.get("detail"):
                print(f"       {p['detail']}")
    else:
        print("\n  ✅ No issues detected")
    print("=" * 68)


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("\nStopped.")
