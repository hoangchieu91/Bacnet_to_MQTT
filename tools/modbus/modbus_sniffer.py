"""Modbus RTU Raw Frame Sniffer & Bus Health Analyzer

Hoạt động như một PASSIVE listener — không gửi gì ra bus.
Đọc raw bytes từ serial port, parse Modbus RTU frames,
rồi phân tích và chẩn đoán sức khỏe bus.

Phát hiện:
  • Slave không trả lời (request timeout)
  • Chatty master (poll quá nhanh)
  • CRC errors (dây kém, nhiễu điện)
  • Exception responses (illegal function/address/value)
  • Bus collision (frame chồng chéo)
  • Slow response (>500ms)
  • Bus silence (>5s không có frame)
  • Junk bytes (bytes rác)

CLI:
  python3 modbus_sniffer.py --port /dev/ttyUSB0 --baud 9600 --duration 60
"""

from __future__ import annotations

import asyncio
import collections
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

import serial
import yaml

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Modbus RTU Function Codes
# ═══════════════════════════════════════════════════════════════════════════════

FUNCTION_NAMES: dict[int, str] = {
    0x01: "Read Coils",
    0x02: "Read Discrete Inputs",
    0x03: "Read Holding Registers",
    0x04: "Read Input Registers",
    0x05: "Write Single Coil",
    0x06: "Write Single Register",
    0x07: "Read Exception Status",
    0x08: "Diagnostics",
    0x0F: "Write Multiple Coils",
    0x10: "Write Multiple Registers",
    0x11: "Report Server ID",
    0x14: "Read File Record",
    0x15: "Write File Record",
    0x16: "Mask Write Register",
    0x17: "Read/Write Multiple Registers",
    0x2B: "Encapsulated Interface Transport",
}

EXCEPTION_NAMES: dict[int, str] = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Server Device Failure",
    0x05: "Acknowledge",
    0x06: "Server Device Busy",
    0x08: "Memory Parity Error",
    0x0A: "Gateway Path Unavailable",
    0x0B: "Gateway Target Failed to Respond",
}

# Expected response lengths for common function codes (request side)
# Format: {func_code: fixed_length_including_slave_func_crc}
# For variable-length, we use data-driven detection
REQUEST_FIXED_LENS = {
    0x01: 8, 0x02: 8, 0x03: 8, 0x04: 8,  # Read: slave+func+addr(2)+qty(2)+crc(2)
    0x05: 8, 0x06: 8,                      # Write single: same structure
}


# ═══════════════════════════════════════════════════════════════════════════════
# CRC-16 (Modbus) — polynomial 0xA001, LSB-first
# ═══════════════════════════════════════════════════════════════════════════════

def _build_crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return table

_CRC16_TABLE = _build_crc16_table()

def _crc16(data: bytes | bytearray) -> int:
    """Modbus CRC-16: returns 16-bit CRC, LSB first."""
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc


# ═══════════════════════════════════════════════════════════════════════════════
# Frame model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ModbusFrame:
    """One parsed Modbus RTU frame."""
    ts: float            # timestamp (time.perf_counter)
    slave_id: int        # 1-247 (0=broadcast, 248-255=reserved)
    function_code: int   # raw function code (bit 7 set = exception)
    data: bytes          # payload between func code and CRC
    crc_ok: bool
    raw_len: int         # total bytes consumed

    @property
    def func_name(self) -> str:
        fc = self.function_code & 0x7F
        return FUNCTION_NAMES.get(fc, f"0x{fc:02X}")

    @property
    def is_exception(self) -> bool:
        return bool(self.function_code & 0x80)

    @property
    def exception_code(self) -> int | None:
        if self.is_exception and len(self.data) >= 1:
            return self.data[0]
        return None

    @property
    def exception_name(self) -> str:
        ec = self.exception_code
        if ec is None:
            return ""
        return EXCEPTION_NAMES.get(ec, f"0x{ec:02X}")

    @property
    def is_request(self) -> bool:
        """Heuristic: request if func is read/write and length matches request pattern."""
        if self.is_exception:
            return False
        fc = self.function_code
        # Read requests: always 8 bytes total
        if fc in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06):
            return self.raw_len == 8
        # Write multiple: request has byte count in data
        if fc in (0x0F, 0x10):
            return len(self.data) > 4
        return True  # default: assume request

    @property
    def is_valid(self) -> bool:
        return self.crc_ok


# ═══════════════════════════════════════════════════════════════════════════════
# Frame parser — gap-based detection
# ═══════════════════════════════════════════════════════════════════════════════

class ModbusFrameParser:
    """Parse Modbus RTU frames from a raw byte stream.

    Modbus RTU frames have NO start/stop delimiters — frames are separated
    by silence on the bus (>= 3.5 character times). We buffer bytes and
    try to parse when a gap is detected or enough bytes arrive.
    """

    MIN_FRAME_LEN = 4     # slave(1) + func(1) + crc(2)
    MAX_FRAME_LEN = 256   # Modbus RTU max ADU = 256 bytes

    def __init__(
        self,
        baudrate: int = 9600,
        on_frame: Callable[[ModbusFrame], None] | None = None,
    ):
        self.baudrate = baudrate
        self._on_frame = on_frame
        self._buf = bytearray()
        self._last_byte_ts: float = 0.0
        self._frame_start_ts: float = 0.0
        self.junk_bytes = 0
        self.total_bytes = 0

        # 3.5 char times at current baud rate (minimum inter-frame gap)
        # 1 char = 11 bits (start + 8 data + parity + stop)
        self._gap_s = max(3.5 * 11.0 / baudrate, 0.00175)  # min 1.75ms

    def feed_bytes(self, data: bytes | bytearray, ts: float | None = None) -> None:
        """Feed raw bytes from serial port."""
        now = ts or time.perf_counter()
        for b in data:
            self.total_bytes += 1
            # Check for inter-frame gap
            if self._buf and (now - self._last_byte_ts) > self._gap_s:
                self._try_parse()
            if not self._buf:
                self._frame_start_ts = now
            self._buf.append(b)
            self._last_byte_ts = now

    def flush(self) -> None:
        """Force parse whatever is in the buffer."""
        if self._buf:
            self._try_parse()

    def _try_parse(self) -> None:
        """Try to parse the buffered bytes as a Modbus RTU frame."""
        buf = bytes(self._buf)
        self._buf.clear()

        if len(buf) < self.MIN_FRAME_LEN:
            self.junk_bytes += len(buf)
            return

        if len(buf) > self.MAX_FRAME_LEN:
            self.junk_bytes += len(buf)
            return

        # Extract components
        slave_id = buf[0]
        func_code = buf[1]
        payload = buf[2:-2]
        received_crc = buf[-2] | (buf[-1] << 8)  # LSB first
        expected_crc = _crc16(buf[:-2])
        crc_ok = (received_crc == expected_crc)

        frame = ModbusFrame(
            ts=self._frame_start_ts,
            slave_id=slave_id,
            function_code=func_code,
            data=payload,
            crc_ok=crc_ok,
            raw_len=len(buf),
        )

        if not crc_ok:
            logger.debug("[ModbusSniffer] CRC fail slave=%d func=0x%02X len=%d",
                         slave_id, func_code, len(buf))

        if self._on_frame:
            self._on_frame(frame)


# ═══════════════════════════════════════════════════════════════════════════════
# Health Analyzer — aggregate stats & detect pathologies
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SlaveStats:
    slave_id: int
    total_frames: int = 0
    total_bytes: int = 0
    requests: int = 0
    responses: int = 0
    exceptions: int = 0
    bad_crc_frames: int = 0
    exception_codes: dict[int, int] = field(default_factory=dict)
    function_codes: dict[int, int] = field(default_factory=dict)
    response_times: list[float] = field(default_factory=list)  # ms

    # Derived
    frames_per_sec: float = 0.0
    bytes_per_sec: float = 0.0
    avg_response_ms: float = 0.0
    max_response_ms: float = 0.0


@dataclass
class Pathology:
    """One detected bus problem."""
    severity: str     # 'critical' | 'warning' | 'info'
    code: str
    description: str
    slaves_involved: list[int] = field(default_factory=list)
    detail: str = ""


class ModbusHealthAnalyzer:
    """Accumulates frame statistics and runs diagnostic rules."""

    # Thresholds
    CHATTY_THRESHOLD_RPS      = 50.0    # requests/sec → noisy
    CRC_ERROR_WARN_PCT        = 5.0
    CRC_ERROR_CRIT_PCT        = 15.0
    SLOW_RESPONSE_MS          = 500.0
    SILENCE_WARN_MS           = 5000.0
    SILENCE_CRIT_MS           = 15000.0
    JUNK_RATE_WARN            = 0.02
    EXCEPTION_RATE_WARN       = 0.10    # 10% exception responses

    def __init__(self, baudrate: int = 9600):
        self.baudrate = baudrate
        self._slaves: dict[int, SlaveStats] = {}
        self._start_time = time.perf_counter()
        self._total_frames = 0
        self._bad_frames = 0
        self._junk_bytes = 0
        self._total_bytes = 0
        self._total_requests = 0
        self._total_responses = 0
        self._total_exceptions = 0
        self._last_frame_ts: float = time.perf_counter()
        self._max_silence_ms: float = 0.0

        # Request tracking for response time: {slave_id: (ts, func_code)}
        self._pending_requests: dict[int, tuple[float, int]] = {}

        # Timeline for dashboard
        self._window_frames: collections.deque[tuple[float, int]] = collections.deque()

        self.pathologies: list[Pathology] = []

    # ── Ingest ─────────────────────────────────────────────────────────────

    def ingest_frame(self, frame: ModbusFrame) -> None:
        now = frame.ts

        # Track silence
        silence_ms = (now - self._last_frame_ts) * 1000
        if silence_ms > self._max_silence_ms:
            self._max_silence_ms = silence_ms
        self._last_frame_ts = now

        self._total_frames += 1
        self._total_bytes += frame.raw_len
        self._window_frames.append((now, frame.slave_id))
        cutoff = now - 1.0
        while self._window_frames and self._window_frames[0][0] < cutoff:
            self._window_frames.popleft()

        # Slave stats
        ss = self._slaves.setdefault(frame.slave_id, SlaveStats(slave_id=frame.slave_id))
        ss.total_frames += 1
        ss.total_bytes += frame.raw_len

        fc = frame.function_code & 0x7F
        ss.function_codes[fc] = ss.function_codes.get(fc, 0) + 1

        if frame.crc_ok is False:
            ss.bad_crc_frames += 1
            self._bad_frames += 1

        if frame.is_exception:
            ss.exceptions += 1
            self._total_exceptions += 1
            ec = frame.exception_code
            if ec is not None:
                ss.exception_codes[ec] = ss.exception_codes.get(ec, 0) + 1
            ss.responses += 1
            self._total_responses += 1
            # Match to pending request
            pending = self._pending_requests.pop(frame.slave_id, None)
            if pending:
                resp_ms = (now - pending[0]) * 1000
                ss.response_times.append(resp_ms)
                if len(ss.response_times) > 200:
                    ss.response_times = ss.response_times[-200:]
        elif frame.is_request:
            ss.requests += 1
            self._total_requests += 1
            # Track as pending for response time measurement
            if frame.slave_id != 0:  # Not broadcast
                self._pending_requests[frame.slave_id] = (now, frame.function_code)
        else:
            ss.responses += 1
            self._total_responses += 1
            # Match to pending request
            pending = self._pending_requests.pop(frame.slave_id, None)
            if pending:
                resp_ms = (now - pending[0]) * 1000
                ss.response_times.append(resp_ms)
                if len(ss.response_times) > 200:
                    ss.response_times = ss.response_times[-200:]

    def set_junk_bytes(self, count: int, total: int) -> None:
        self._junk_bytes = count
        self._total_bytes = max(self._total_bytes, total)

    # ── Diagnostics ────────────────────────────────────────────────────────

    def run_diagnostics(self) -> list[Pathology]:
        self.pathologies = []
        elapsed = time.perf_counter() - self._start_time
        if elapsed < 2.0:
            return []

        self._check_chatty_master(elapsed)
        self._check_bad_crc_rate()
        self._check_junk_rate()
        self._check_exception_rate()
        self._check_slow_responses()
        self._check_no_response()
        self._check_bus_silence()
        self._check_bus_utilisation(elapsed)

        return self.pathologies

    def _add(self, p: Pathology, once: bool = False) -> None:
        if once:
            for ex in self.pathologies:
                if ex.code == p.code:
                    return
        self.pathologies.append(p)

    def _check_chatty_master(self, elapsed: float) -> None:
        for sid, ss in self._slaves.items():
            rps = ss.requests / elapsed
            if rps > self.CHATTY_THRESHOLD_RPS:
                sev = "critical" if rps > self.CHATTY_THRESHOLD_RPS * 2 else "warning"
                self._add(Pathology(
                    severity=sev, code="CHATTY_MASTER",
                    description=f"Slave {sid} receiving {rps:.1f} requests/s",
                    slaves_involved=[sid],
                    detail=f"Threshold: {self.CHATTY_THRESHOLD_RPS}/s. "
                           "Reduce poll frequency or increase interval.",
                ))

    def _check_bad_crc_rate(self) -> None:
        if self._total_frames < 10:
            return
        rate = self._bad_frames / self._total_frames * 100
        if rate > self.CRC_ERROR_WARN_PCT:
            sev = "critical" if rate > self.CRC_ERROR_CRIT_PCT else "warning"
            worst = sorted(self._slaves.values(), key=lambda s: s.bad_crc_frames, reverse=True)[:3]
            self._add(Pathology(
                severity=sev, code="HIGH_CRC_ERRORS",
                description=f"CRC error rate {rate:.1f}%",
                slaves_involved=[s.slave_id for s in worst if s.bad_crc_frames > 0],
                detail=f"{self._bad_frames}/{self._total_frames} bad. "
                       "Check: wiring, termination, baud mismatch, EMI.",
            ))

    def _check_junk_rate(self) -> None:
        if self._total_bytes < 100:
            return
        rate = self._junk_bytes / self._total_bytes
        if rate > self.JUNK_RATE_WARN:
            sev = "critical" if rate > 0.10 else "warning"
            self._add(Pathology(
                severity=sev, code="JUNK_BYTES",
                description=f"Junk byte rate {rate*100:.1f}%",
                detail=f"{self._junk_bytes} junk out of {self._total_bytes} total. "
                       "Likely: baud mismatch or electrical noise.",
            ))

    def _check_exception_rate(self) -> None:
        if self._total_responses < 10:
            return
        rate = self._total_exceptions / self._total_responses
        if rate > self.EXCEPTION_RATE_WARN:
            sev = "critical" if rate > 0.25 else "warning"
            worst = sorted(self._slaves.values(), key=lambda s: s.exceptions, reverse=True)[:3]
            exc_summary = {}
            for ss in self._slaves.values():
                for ec, cnt in ss.exception_codes.items():
                    name = EXCEPTION_NAMES.get(ec, f"0x{ec:02X}")
                    exc_summary[name] = exc_summary.get(name, 0) + cnt
            detail_parts = [f"{name}: {cnt}" for name, cnt in exc_summary.items()]
            self._add(Pathology(
                severity=sev, code="EXCEPTION_RESPONSES",
                description=f"Exception response rate {rate*100:.1f}%",
                slaves_involved=[s.slave_id for s in worst if s.exceptions > 0],
                detail="; ".join(detail_parts) if detail_parts else "",
            ))

    def _check_slow_responses(self) -> None:
        for sid, ss in self._slaves.items():
            if not ss.response_times:
                continue
            max_rt = max(ss.response_times)
            avg_rt = sum(ss.response_times) / len(ss.response_times)
            ss.avg_response_ms = round(avg_rt, 1)
            ss.max_response_ms = round(max_rt, 1)
            if max_rt > self.SLOW_RESPONSE_MS:
                self._add(Pathology(
                    severity="warning", code="SLOW_RESPONSE",
                    description=f"Slave {sid} max response {max_rt:.0f}ms (avg {avg_rt:.0f}ms)",
                    slaves_involved=[sid],
                    detail=f"Threshold: {self.SLOW_RESPONSE_MS}ms. "
                           "Device may be overloaded.",
                ))

    def _check_no_response(self) -> None:
        elapsed = time.perf_counter() - self._start_time
        for sid, (ts, fc) in list(self._pending_requests.items()):
            age_ms = (time.perf_counter() - ts) * 1000
            if age_ms > 5000:  # 5s timeout
                self._add(Pathology(
                    severity="warning", code="SLAVE_NO_RESPONSE",
                    description=f"Slave {sid} not responding (pending {age_ms:.0f}ms)",
                    slaves_involved=[sid],
                    detail=f"Function: {FUNCTION_NAMES.get(fc, hex(fc))}. "
                           "Check: slave power, address config, wiring.",
                ))
                del self._pending_requests[sid]

    def _check_bus_silence(self) -> None:
        if self._max_silence_ms > self.SILENCE_WARN_MS:
            sev = "critical" if self._max_silence_ms > self.SILENCE_CRIT_MS else "warning"
            self._add(Pathology(
                severity=sev, code="BUS_SILENCE",
                description=f"Bus silent for {self._max_silence_ms:.0f}ms",
                detail="No frames received. Master may be offline or bus disconnected.",
            ), once=True)

    def _check_bus_utilisation(self, elapsed: float) -> None:
        if elapsed < 1.0:
            return
        bits_used = self._total_bytes * 11  # 11 bits per char (Modbus RTU)
        bandwidth = self.baudrate * elapsed
        util_pct = (bits_used / bandwidth) * 100
        if util_pct > 70:
            sev = "critical" if util_pct > 90 else "warning"
            self._add(Pathology(
                severity=sev, code="HIGH_BUS_UTILIZATION",
                description=f"Bus utilization {util_pct:.1f}% at {self.baudrate} baud",
                detail="Consider: increase baud rate, reduce poll frequency.",
            ))

    # ── Report ─────────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        elapsed = max(time.perf_counter() - self._start_time, 0.001)
        self.run_diagnostics()

        slaves_summary = []
        for sid, ss in sorted(self._slaves.items()):
            ss.frames_per_sec = round(ss.total_frames / elapsed, 2)
            ss.bytes_per_sec = round(ss.total_bytes / elapsed, 1)
            avg_rt = round(sum(ss.response_times) / len(ss.response_times), 1) if ss.response_times else None
            max_rt = round(max(ss.response_times), 1) if ss.response_times else None
            slaves_summary.append({
                "slave_id":       sid,
                "total_frames":   ss.total_frames,
                "frames_per_s":   ss.frames_per_sec,
                "bytes_per_s":    ss.bytes_per_sec,
                "requests":       ss.requests,
                "responses":      ss.responses,
                "exceptions":     ss.exceptions,
                "bad_crc":        ss.bad_crc_frames,
                "bad_crc_pct":    round(ss.bad_crc_frames / max(ss.total_frames, 1) * 100, 1),
                "avg_resp_ms":    avg_rt,
                "max_resp_ms":    max_rt,
                "exception_codes": {
                    EXCEPTION_NAMES.get(ec, hex(ec)): cnt
                    for ec, cnt in ss.exception_codes.items()
                },
                "function_codes": {
                    FUNCTION_NAMES.get(fc, hex(fc)): cnt
                    for fc, cnt in ss.function_codes.items()
                },
            })

        bits_used = self._total_bytes * 11
        bandwidth = self.baudrate * elapsed
        util_pct = round(min(bits_used / bandwidth * 100, 100.0), 1)
        junk_rate = (self._junk_bytes / max(self._total_bytes, 1)) * 100

        return {
            "duration_s":          round(elapsed, 1),
            "total_frames":        self._total_frames,
            "total_requests":      self._total_requests,
            "total_responses":     self._total_responses,
            "total_exceptions":    self._total_exceptions,
            "bad_frames":          self._bad_frames,
            "bad_frame_pct":       round(self._bad_frames / max(self._total_frames, 1) * 100, 1),
            "junk_bytes":          self._junk_bytes,
            "junk_rate_pct":       round(junk_rate, 2),
            "total_bytes":         self._total_bytes,
            "frames_per_s":        round(self._total_frames / elapsed, 1),
            "bytes_per_s":         round(self._total_bytes / elapsed, 0),
            "bus_utilization_pct": util_pct,
            "baudrate":            self.baudrate,
            "max_silence_ms":      round(self._max_silence_ms, 1),
            "slave_count":         len(self._slaves),
            "slaves":              slaves_summary,
            "pathologies": [
                {
                    "severity":         p.severity,
                    "code":             p.code,
                    "description":      p.description,
                    "slaves_involved":  p.slaves_involved,
                    "detail":           p.detail,
                }
                for p in sorted(self.pathologies,
                                key=lambda x: {"critical": 0, "warning": 1, "info": 2}[x.severity])
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Sniffer — ties parser + analyzer + serial port together
# ═══════════════════════════════════════════════════════════════════════════════

class ModbusSniffer:
    """Passive Modbus RTU bus sniffer.

    Reads raw bytes from serial port, parses frames, feeds analyzer.
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        on_frame: Callable[[ModbusFrame], None] | None = None,
        on_pathology: Callable[[Pathology], None] | None = None,
        diag_interval: float = 5.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self._on_frame = on_frame
        self._on_pathology = on_pathology
        self._diag_interval = diag_interval

        self.analyzer = ModbusHealthAnalyzer(baudrate=baudrate)
        self._parser = ModbusFrameParser(baudrate=baudrate, on_frame=self._handle_frame)
        self._serial: serial.Serial | None = None
        self._running = False
        self._read_task: asyncio.Task | None = None
        self._diag_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self.frame_log: list[dict] = []

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", **kwargs) -> "ModbusSniffer":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        serial_cfg = cfg.get("serial", {})
        sniffer_cfg = cfg.get("sniffer", {})
        return cls(
            port=serial_cfg.get("port", "/dev/ttyUSB0"),
            baudrate=serial_cfg.get("baudrate", 9600),
            parity=serial_cfg.get("parity", "N"),
            stopbits=serial_cfg.get("stopbits", 1),
            diag_interval=sniffer_cfg.get("diag_interval_s", 5.0),
            **kwargs,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._open_serial)
        self._running = True
        self._read_task = asyncio.create_task(self._read_loop())
        self._diag_task = asyncio.create_task(self._diag_loop())
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("[ModbusSniffer] Started on %s @ %d baud (PASSIVE)", self.port, self.baudrate)

    async def stop(self) -> None:
        self._running = False
        for task in (self._read_task, self._diag_task, self._flush_task):
            if task and not task.done():
                task.cancel()
        if self._serial and self._serial.is_open:
            self._serial.close()
        logger.info("[ModbusSniffer] Stopped")

    def _open_serial(self) -> None:
        parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
        stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=parity_map.get(self.parity, serial.PARITY_NONE),
            stopbits=stop_map.get(self.stopbits, serial.STOPBITS_ONE),
            timeout=0.05,
        )
        logger.info("[ModbusSniffer] Serial open: %s", self.port)

    # ── Read loop ──────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                data = await loop.run_in_executor(None, self._serial.read, 256)
                if data:
                    self._parser.feed_bytes(data)
                    self.analyzer.set_junk_bytes(
                        self._parser.junk_bytes,
                        self._parser.total_bytes,
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[ModbusSniffer] Read error: %s", exc)
                await asyncio.sleep(0.5)

    async def _flush_loop(self) -> None:
        """Periodically flush the parser buffer to handle end-of-transmission."""
        while self._running:
            await asyncio.sleep(0.1)
            try:
                self._parser.flush()
            except Exception:
                pass

    async def _diag_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._diag_interval)
            try:
                pathologies = self.analyzer.run_diagnostics()
                if pathologies and self._on_pathology:
                    for p in pathologies:
                        self._on_pathology(p)
            except Exception as exc:
                logger.debug("[ModbusSniffer] Diag error: %s", exc)

    # ── Frame handler ──────────────────────────────────────────────────────

    def _handle_frame(self, frame: ModbusFrame) -> None:
        self.analyzer.ingest_frame(frame)
        # Reconstruct raw bytes for decoder
        raw_bytes = bytes([frame.slave_id, frame.function_code]) + frame.data
        log_entry = {
            "ts":       round(frame.ts, 4),
            "slave":    frame.slave_id,
            "func":     frame.func_name,
            "fc":       frame.function_code,
            "len":      len(frame.data),
            "valid":    frame.is_valid,
            "is_req":   frame.is_request,
            "is_exc":   frame.is_exception,
            "exc_name": frame.exception_name if frame.is_exception else "",
            "raw_hex":  raw_bytes.hex(),
            "data_hex": frame.data.hex(),
        }
        self.frame_log.append(log_entry)
        if len(self.frame_log) > 500:
            self.frame_log = self.frame_log[-500:]
        if self._on_frame:
            self._on_frame(frame)

    # ── Report ─────────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        return self.analyzer.get_report()

    def get_frame_log(self, limit: int = 100) -> list[dict]:
        return self.frame_log[-limit:]


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═══════════════════════════════════════════════════════════════════════════════

async def _cli_main() -> None:
    import argparse, json

    parser = argparse.ArgumentParser(description="Modbus RTU Sniffer & Bus Health Analyzer")
    parser.add_argument("--port",     default="/dev/ttyUSB0")
    parser.add_argument("--baud",     type=int, default=9600)
    parser.add_argument("--parity",   default="N", choices=["N", "E", "O"])
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--json",     action="store_true")
    parser.add_argument("--frames",   action="store_true")
    parser.add_argument("--config",   default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    def frame_printer(frame: ModbusFrame) -> None:
        if args.frames:
            status = "✓" if frame.is_valid else "✗"
            kind = "EXC" if frame.is_exception else ("REQ" if frame.is_request else "RSP")
            print(f"  {status} [{kind}] slave={frame.slave_id:3d} "
                  f"func={frame.func_name:24s} data={len(frame.data):3d}B"
                  f"{' → '+frame.exception_name if frame.is_exception else ''}")

    if args.config:
        sniffer = ModbusSniffer.from_config(args.config, on_frame=frame_printer)
    else:
        sniffer = ModbusSniffer(port=args.port, baudrate=args.baud, parity=args.parity,
                                on_frame=frame_printer)

    print(f"\n📟 Modbus RTU Sniffer — {sniffer.port} @ {sniffer.baudrate} baud")
    print(f"   Capturing for {args.duration}s  (Ctrl+C to stop early)\n")

    await sniffer.start()
    try:
        for i in range(args.duration):
            await asyncio.sleep(1)
            r = sniffer.get_report()
            print(f"\r  ⏱  {i+1:3d}s  |"
                  f"  frames: {r['total_frames']:5d}  |"
                  f"  req: {r['total_requests']:4d}  |"
                  f"  rsp: {r['total_responses']:4d}  |"
                  f"  exc: {r['total_exceptions']:3d}  |"
                  f"  bad: {r['bad_frames']:3d}  |"
                  f"  slaves: {r['slave_count']:3d}",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        await sniffer.stop()

    print("\n")
    report = sniffer.get_report()

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 64)
    print("  Modbus RTU Bus Health Report")
    print("=" * 64)
    print(f"  Duration:        {report['duration_s']:.0f}s")
    print(f"  Total frames:    {report['total_frames']} ({report['frames_per_s']}/s)")
    print(f"  Requests:        {report['total_requests']}")
    print(f"  Responses:       {report['total_responses']}")
    print(f"  Exceptions:      {report['total_exceptions']}")
    print(f"  Bad CRC:         {report['bad_frames']} ({report['bad_frame_pct']}%)")
    print(f"  Bus utilization: {report['bus_utilization_pct']}%")
    print(f"  Max silence:     {report['max_silence_ms']}ms")
    print(f"  Slaves seen:     {report['slave_count']}")

    print(f"\n  {'SID':>4}  {'FPS':>6}  {'Req':>5}  {'Rsp':>5}  {'Exc':>4}  {'CRC%':>5}  {'AvgMs':>6}  {'MaxMs':>6}")
    print(f"  {'─'*4}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*4}  {'─'*5}  {'─'*6}  {'─'*6}")
    for s in report["slaves"]:
        print(f"  {s['slave_id']:4d}  {s['frames_per_s']:6.1f}  "
              f"{s['requests']:5d}  {s['responses']:5d}  "
              f"{s['exceptions']:4d}  {s['bad_crc_pct']:5.1f}  "
              f"{str(s['avg_resp_ms'] or '—'):>6}  "
              f"{str(s['max_resp_ms'] or '—'):>6}")

    if report["pathologies"]:
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        print(f"\n  {'─'*60}")
        print(f"  DIAGNOSTICS ({len(report['pathologies'])} issues):")
        for p in report["pathologies"]:
            icon = icons.get(p["severity"], "•")
            print(f"  {icon} [{p['code']}] {p['description']}")
            if p["detail"]:
                print(f"       {p['detail']}")
    else:
        print("\n  ✅ No issues detected")
    print("=" * 64)


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("\nStopped.")
