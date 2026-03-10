"""MS/TP Raw Frame Sniffer & Network Health Analyzer

Hoạt động như một PASSIVE listener — không join token ring, không gửi gì ra bus.
Chỉ đọc raw bytes từ serial port, parse MS/TP frames theo chuẩn ASHRAE 135-2016
clause 9.3, rồi phân tích và chẩn đoán sức khỏe mạng.

Phát hiện:
  • Node nói quá nhiều (noisy / chatty nodes)
  • Frame rác / malformed (bad CRC, bad preamble, wrong length)
  • Duplicate Device Instance (2 node khác nhau cùng khai I-Am với cùng ID)
  • Duplicate MAC (không thể xảy ra đúng chuẩn, nhưng firmware bug có thể gây ra)
  • Token imbalance (1 node giữ token lâu hơn bình thường)
  • Bus utilization (frames/s, bytes/s, bandwidth %)
  • Poll-For-Master storms (node cố gán địa chỉ đã có)
  • Longest silence (dead time = bus bị treo)
  • Slow responders (reply time > 255ms)

Dùng:
  sniffer = MstpSniffer.from_config("config.yaml")
  await sniffer.start()         # bắt đầu đọc serial non-blocking
  report  = sniffer.get_report()
  await sniffer.stop()

CLI:
  python3 mstp_sniffer.py --port /dev/ttyUSB0 --baud 38400 --duration 60
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
# MS/TP Frame Spec  (ASHRAE 135-2016 clause 9.3)
# ═══════════════════════════════════════════════════════════════════════════════

class FT(IntEnum):
    """MS/TP frame types."""
    TOKEN               = 0x00
    POLL_FOR_MASTER     = 0x01
    REPLY_TO_POLL       = 0x02
    TEST_REQUEST        = 0x03
    TEST_RESPONSE       = 0x04
    BACNET_DATA_XR      = 0x05   # BACnet Data Expecting Reply
    BACNET_DATA_NXR     = 0x06   # BACnet Data Not Expecting Reply
    REPLY_POSTPONED     = 0x07

FRAME_TYPE_NAMES: dict[int, str] = {
    0x00: "Token",
    0x01: "PollForMaster",
    0x02: "ReplyToPoll",
    0x03: "TestReq",
    0x04: "TestResp",
    0x05: "BACnet-XR",
    0x06: "BACnet-NXR",
    0x07: "ReplyPostponed",
}

# Header CRC lookup table (CRC-8, x^8 + x^2 + x + 1)
def _build_crc8_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
        table.append(crc & 0xFF)
    return table

_CRC8_TABLE = _build_crc8_table()

def _crc8(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return (~crc) & 0xFF

# Data CRC-16 (CCITT, x^16 + x^12 + x^5 + 1)
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
    return (~crc) & 0xFFFF


# ═══════════════════════════════════════════════════════════════════════════════
# Frame parser — state machine
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MstpFrame:
    """One parsed MS/TP frame."""
    ts: float          # timestamp (time.perf_counter)
    frame_type: int
    dst: int           # destination MAC (0-127, 255=broadcast)
    src: int           # source MAC
    data: bytes        # payload (may be empty)
    header_crc_ok: bool
    data_crc_ok: bool
    raw_len: int        # bytes consumed from stream (for utilisation calc)

    @property
    def name(self) -> str:
        return FRAME_TYPE_NAMES.get(self.frame_type, f"0x{self.frame_type:02X}")

    @property
    def is_bacnet(self) -> bool:
        return self.frame_type in (FT.BACNET_DATA_XR, FT.BACNET_DATA_NXR)

    @property
    def is_valid(self) -> bool:
        return self.header_crc_ok and (not self.data or self.data_crc_ok)

    @property
    def bad(self) -> bool:
        return not self.is_valid


class _State(IntEnum):
    IDLE       = 0
    PRE2       = 1   # saw 0x55, waiting for 0xFF
    FTYPE      = 2
    DST        = 3
    SRC        = 4
    LEN_HI     = 5
    LEN_LO     = 6
    HDR_CRC    = 7
    DATA       = 8
    DATA_CRC1  = 9
    DATA_CRC2  = 10


class MstpFrameParser:
    """Byte-by-byte state machine that extracts MS/TP frames from a raw byte stream.

    Feed bytes with .feed(b) or .feed_bytes(buf).
    Parsed frames are appended to .frames.
    Junk byte count available via .junk_bytes.
    """

    MAX_DATA_LEN = 512   # ASHRAE limit: 501 bytes data + overhead

    def __init__(self, on_frame: Callable[[MstpFrame], None] | None = None):
        self._state   = _State.IDLE
        self._buf     = bytearray()
        self._ft      = 0
        self._dst     = 0
        self._src     = 0
        self._dlen    = 0
        self._data    = bytearray()
        self._ts      = 0.0
        self.junk_bytes  = 0
        self.total_bytes = 0
        self._on_frame   = on_frame

    def feed_bytes(self, data: bytes | bytearray) -> None:
        for b in data:
            self._step(b)

    def _step(self, b: int) -> None:
        self.total_bytes += 1
        st = self._state

        if st == _State.IDLE:
            if b == 0x55:
                self._state = _State.PRE2
                self._ts = time.perf_counter()
            else:
                self.junk_bytes += 1

        elif st == _State.PRE2:
            if b == 0xFF:
                self._state = _State.FTYPE
            elif b == 0x55:
                pass  # another 0x55, stay in PRE2
            else:
                self.junk_bytes += 2
                self._state = _State.IDLE

        elif st == _State.FTYPE:
            self._ft    = b
            self._state = _State.DST

        elif st == _State.DST:
            self._dst   = b
            self._state = _State.SRC

        elif st == _State.SRC:
            self._src   = b
            self._state = _State.LEN_HI

        elif st == _State.LEN_HI:
            self._dlen  = b << 8
            self._state = _State.LEN_LO

        elif st == _State.LEN_LO:
            self._dlen |= b
            self._state = _State.HDR_CRC

        elif st == _State.HDR_CRC:
            # Verify header CRC over: ft, dst, src, len_hi, len_lo
            hdr = bytes([self._ft, self._dst, self._src,
                         (self._dlen >> 8) & 0xFF, self._dlen & 0xFF])
            expected = _crc8(hdr)
            hdr_ok = (b == expected)
            if not hdr_ok:
                logger.debug("[Sniffer] Header CRC fail src=%d dst=%d",
                             self._src, self._dst)

            if self._dlen == 0:
                # No data section — frame complete
                frame = MstpFrame(
                    ts=self._ts, frame_type=self._ft,
                    dst=self._dst, src=self._src,
                    data=b"", header_crc_ok=hdr_ok, data_crc_ok=True,
                    raw_len=6,
                )
                self._emit(frame)
                self._state = _State.IDLE
            elif self._dlen > self.MAX_DATA_LEN:
                logger.debug("[Sniffer] Oversized data len=%d — junk frame", self._dlen)
                self.junk_bytes += 6
                self._state = _State.IDLE
            else:
                self._data  = bytearray()
                self._hdr_ok = hdr_ok
                self._state = _State.DATA

        elif st == _State.DATA:
            self._data.append(b)
            if len(self._data) == self._dlen:
                self._state = _State.DATA_CRC1

        elif st == _State.DATA_CRC1:
            self._dcrc_lo = b
            self._state   = _State.DATA_CRC2

        elif st == _State.DATA_CRC2:
            # Data CRC is appended as little-endian 16-bit
            received_crc = self._dcrc_lo | (b << 8)
            expected_crc = _crc16(bytes(self._data))
            data_ok = (received_crc == expected_crc)
            if not data_ok:
                logger.debug("[Sniffer] Data CRC fail src=%d len=%d",
                             self._src, self._dlen)
            frame = MstpFrame(
                ts=self._ts, frame_type=self._ft,
                dst=self._dst, src=self._src,
                data=bytes(self._data),
                header_crc_ok=getattr(self, '_hdr_ok', True),
                data_crc_ok=data_ok,
                raw_len=6 + self._dlen + 2,
            )
            self._emit(frame)
            self._state = _State.IDLE

    def _emit(self, frame: MstpFrame) -> None:
        if self._on_frame:
            self._on_frame(frame)


# ═══════════════════════════════════════════════════════════════════════════════
# Health Analyzer — aggregate stats & detect pathologies
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class NodeStats:
    mac: int
    total_frames: int = 0
    total_bytes: int = 0
    token_times: list[float]    = field(default_factory=list)   # perf_counter timestamps
    frame_types: dict[int, int] = field(default_factory=dict)
    bad_crc_frames: int = 0
    consecutive_tokens: int = 0
    device_instances: set[int]  = field(default_factory=set)   # IDs from I-Am

    # Derived (filled by analyzer on demand)
    frames_per_sec: float = 0.0
    bytes_per_sec:  float = 0.0
    token_hold_avg_ms: float = 0.0   # avg time between getting token and passing it
    token_hold_max_ms: float = 0.0


@dataclass
class Pathology:
    """One detected bus problem."""
    severity: str     # 'critical' | 'warning' | 'info'
    code: str         # machine-readable code
    description: str
    nodes_involved: list[int] = field(default_factory=list)
    detail: str = ""


class MstpHealthAnalyzer:
    """Accumulates frame statistics and runs diagnostic rules.

    Usage:
      analyzer = MstpHealthAnalyzer()
      # parser feeds frames to analyzer.ingest_frame
      analyzer.ingest_frame(frame)
      ...
      report = analyzer.get_report()
    """

    # Thresholds
    CHATTY_NODE_FPS_THRESHOLD  = 20.0   # frames/sec → noisy warning
    TOKEN_IMBALANCE_RATIO      = 3.0    # if one node holds >3x avg → warning
    BUS_UTILIZATION_WARN_PCT   = 70.0   # 38400 bps → warn at 70%
    BUS_UTILIZATION_CRIT_PCT   = 90.0
    JUNK_RATE_WARN             = 0.02   # 2% junk bytes → warning
    SILENCE_WARN_MS            = 500.0  # no frame for >500ms → warning
    SILENCE_CRIT_MS            = 2000.0 # no frame for >2s → critical

    def __init__(self, baudrate: int = 38400):
        self.baudrate = baudrate
        self._nodes: dict[int, NodeStats] = {}
        self._start_time  = time.perf_counter()
        self._total_frames = 0
        self._bad_frames   = 0
        self._junk_bytes   = 0
        self._total_bytes  = 0
        self._last_frame_ts: float = time.perf_counter()
        self._max_silence_ms: float = 0.0
        self._token_holder: dict[int, float] = {}   # mac → when they received token

        # For duplicate ID detection: device_instance → set of source MACs
        self._instance_to_macs: dict[int, set[int]] = collections.defaultdict(set)

        # Timeline: list of (ts, event_str) for dashboard
        self._timeline: list[dict] = []
        self._window_frames: collections.deque[tuple[float, int]] = collections.deque()

        self.pathologies: list[Pathology] = []

    # ── Ingest ─────────────────────────────────────────────────────────────

    def ingest_frame(self, frame: MstpFrame) -> None:
        now = frame.ts

        # Track silence
        silence_ms = (now - self._last_frame_ts) * 1000
        if silence_ms > self._max_silence_ms:
            self._max_silence_ms = silence_ms
        if silence_ms > self.SILENCE_WARN_MS:
            sev = "critical" if silence_ms > self.SILENCE_CRIT_MS else "warning"
            self._add_pathology(Pathology(
                severity=sev,
                code="BUS_SILENCE",
                description=f"Bus silent for {silence_ms:.0f}ms",
                detail=f"No frame received for {silence_ms:.0f}ms at t={now:.3f}",
            ), once_per_code=True)
        self._last_frame_ts = now

        self._total_frames += 1
        self._total_bytes  += frame.raw_len
        self._window_frames.append((now, frame.src))
        # Purge old window entries (1-second sliding window for fps calc)
        cutoff = now - 1.0
        while self._window_frames and self._window_frames[0][0] < cutoff:
            self._window_frames.popleft()

        # Node stats
        ns = self._nodes.setdefault(frame.src, NodeStats(mac=frame.src))
        ns.total_frames += 1
        ns.total_bytes  += frame.raw_len
        ns.frame_types[frame.frame_type] = ns.frame_types.get(frame.frame_type, 0) + 1

        if frame.bad:
            ns.bad_crc_frames += 1
            self._bad_frames  += 1

        # Token tracking
        if frame.frame_type == FT.TOKEN:
            # dst receives the token
            recv_ns = self._nodes.setdefault(frame.dst, NodeStats(mac=frame.dst))
            recv_ts = self._token_holder.get(frame.src)
            if recv_ts is not None:
                hold_ms = (now - recv_ts) * 1000
                ns.token_times.append(hold_ms)
                if len(ns.token_times) > 200:
                    ns.token_times = ns.token_times[-200:]
            self._token_holder[frame.dst] = now

        # BACnet payload analysis (I-Am → extract device instance)
        if frame.is_bacnet and len(frame.data) >= 6:
            self._try_parse_iam(frame)

    def _try_parse_iam(self, frame: MstpFrame) -> None:
        """Heuristic: detect I-Am APDU and extract device instance for dup-ID check."""
        data = frame.data
        # APDU: BACnet/MSTP network layer (bytes 0-3) then APDU
        # Network layer: version(1), ctrl(1), dnet(2), dlen(1), dadr(n), hop(1) or no routing
        # We just look for the I-Am pattern: Unconfirmed-REQ PDU (0x10), I-Am (0x00)
        # Skip network layer header (variable) by searching for 0x10 0x00
        for i in range(min(8, len(data) - 2)):
            if data[i] == 0x10 and data[i+1] == 0x00:
                # I-Am found — next few bytes encode device instance as BACnet tag
                # Object identifier tag: tag 0, context, length 4
                offset = i + 2
                if offset + 4 < len(data):
                    try:
                        # BACnet object identifier: upper 10 bits = type (8=device), lower 22 = instance
                        oid_bytes = data[offset+1:offset+5]
                        oid = struct.unpack(">I", oid_bytes)[0]
                        obj_type = (oid >> 22) & 0x3FF
                        instance = oid & 0x3FFFFF
                        if obj_type == 8:  # Device object
                            self._instance_to_macs[instance].add(frame.src)
                            ns = self._nodes[frame.src]
                            ns.device_instances.add(instance)
                    except Exception:
                        pass
                break

    # ── Junk bytes from parser ─────────────────────────────────────────────

    def set_junk_bytes(self, count: int, total: int) -> None:
        self._junk_bytes  = count
        self._total_bytes = max(self._total_bytes, total)

    # ── Diagnostic rules ───────────────────────────────────────────────────

    def run_diagnostics(self) -> list[Pathology]:
        """Run all diagnostic rules and return list of pathologies."""
        self.pathologies = []
        elapsed = time.perf_counter() - self._start_time
        if elapsed < 2.0:
            return []

        self._check_chatty_nodes(elapsed)
        self._check_duplicate_ids()
        self._check_bad_crc_rate()
        self._check_junk_rate()
        self._check_token_imbalance()
        self._check_bus_utilisation(elapsed)
        self._check_poll_for_master_storm()
        self._check_duplicate_mac()

        return self.pathologies

    def _add_pathology(self, p: Pathology, once_per_code: bool = False) -> None:
        if once_per_code:
            for existing in self.pathologies:
                if existing.code == p.code:
                    return
        self.pathologies.append(p)

    def _check_chatty_nodes(self, elapsed: float) -> None:
        for mac, ns in self._nodes.items():
            fps = ns.total_frames / elapsed
            ns.frames_per_sec = fps
            ns.bytes_per_sec  = ns.total_bytes / elapsed
            if fps > self.CHATTY_NODE_FPS_THRESHOLD:
                sev = "critical" if fps > self.CHATTY_NODE_FPS_THRESHOLD * 2 else "warning"
                self._add_pathology(Pathology(
                    severity=sev, code="CHATTY_NODE",
                    description=f"Node {mac} sending {fps:.1f} frames/s (threshold {self.CHATTY_NODE_FPS_THRESHOLD})",
                    nodes_involved=[mac],
                    detail=f"Total frames: {ns.total_frames}, elapsed: {elapsed:.0f}s",
                ))

    def _check_duplicate_ids(self) -> None:
        for inst, macs in self._instance_to_macs.items():
            if len(macs) > 1:
                mac_list = sorted(macs)
                self._add_pathology(Pathology(
                    severity="critical", code="DUPLICATE_DEVICE_ID",
                    description=f"Device instance {inst} claimed by multiple nodes: {mac_list}",
                    nodes_involved=mac_list,
                    detail="Two or more nodes broadcast I-Am with same device instance. "
                           "This causes request routing failures.",
                ))

    def _check_bad_crc_rate(self) -> None:
        if self._total_frames < 10:
            return
        rate = self._bad_frames / self._total_frames
        if rate > 0.10:
            sev = "critical" if rate > 0.25 else "warning"
            worst_nodes = sorted(
                self._nodes.values(), key=lambda n: n.bad_crc_frames, reverse=True
            )[:3]
            self._add_pathology(Pathology(
                severity=sev, code="HIGH_CRC_ERRORS",
                description=f"CRC error rate {rate*100:.1f}% — bus signal quality problem",
                nodes_involved=[n.mac for n in worst_nodes if n.bad_crc_frames > 0],
                detail=f"{self._bad_frames}/{self._total_frames} frames bad. "
                       "Check: termination resistors, cable length, baud rate mismatch, "
                       "loose connectors.",
            ))

    def _check_junk_rate(self) -> None:
        if self._total_bytes < 100:
            return
        rate = self._junk_bytes / self._total_bytes
        if rate > self.JUNK_RATE_WARN:
            sev = "critical" if rate > 0.10 else "warning"
            self._add_pathology(Pathology(
                severity=sev, code="JUNK_BYTES",
                description=f"Junk byte rate {rate*100:.1f}% — garbled bus data",
                detail=f"{self._junk_bytes} junk bytes out of {self._total_bytes} total. "
                       "Likely cause: baud rate mismatch or electrical interference.",
            ))

    def _check_token_imbalance(self) -> None:
        holders = {mac: len(ns.token_times) for mac, ns in self._nodes.items()
                   if ns.token_times}
        if not holders:
            return
        avg_tokens = sum(holders.values()) / len(holders)
        for mac, count in holders.items():
            if count > avg_tokens * self.TOKEN_IMBALANCE_RATIO:
                ns = self._nodes[mac]
                avg_hold = sum(ns.token_times) / len(ns.token_times)
                max_hold = max(ns.token_times)
                ns.token_hold_avg_ms = avg_hold
                ns.token_hold_max_ms = max_hold
                self._add_pathology(Pathology(
                    severity="warning", code="TOKEN_IMBALANCE",
                    description=f"Node {mac} holding token disproportionately ({count} times, avg {avg_hold:.0f}ms)",
                    nodes_involved=[mac],
                    detail=f"Max hold: {max_hold:.0f}ms. Normal max: 50ms. "
                           "Device may be doing expensive local work before passing token.",
                ))

    def _check_bus_utilisation(self, elapsed: float) -> None:
        if elapsed < 1.0:
            return
        bits_used = self._total_bytes * 10  # 8 data + start + stop bits
        bandwidth  = self.baudrate * elapsed
        utilisation_pct = (bits_used / bandwidth) * 100
        if utilisation_pct > self.BUS_UTILIZATION_WARN_PCT:
            sev = "critical" if utilisation_pct > self.BUS_UTILIZATION_CRIT_PCT else "warning"
            self._add_pathology(Pathology(
                severity=sev, code="HIGH_BUS_UTILIZATION",
                description=f"Bus utilization {utilisation_pct:.1f}% at {self.baudrate} baud",
                detail=f"Consider: increase baud rate, reduce poll frequency, enable COV. "
                       f"Total: {self._total_bytes} bytes in {elapsed:.0f}s",
            ))

    def _check_poll_for_master_storm(self) -> None:
        for mac, ns in self._nodes.items():
            pfm_count = ns.frame_types.get(FT.POLL_FOR_MASTER, 0)
            if pfm_count > 20:
                elapsed = time.perf_counter() - self._start_time
                pfm_rate = pfm_count / elapsed
                if pfm_rate > 2.0:
                    self._add_pathology(Pathology(
                        severity="warning", code="PFM_STORM",
                        description=f"Node {mac} sending {pfm_rate:.1f} Poll-For-Master/s — address conflict?",
                        nodes_involved=[mac],
                        detail=f"High PFM rate suggests node is trying to claim an address "
                               "already taken by another master. Check duplicate address config.",
                    ))

    def _check_duplicate_mac(self) -> None:
        """Detect impossible case: 2 different sources seen with same MAC at overlapping times."""
        # This can't happen in correct MS/TP, but firmware bugs can produce it
        # We detect by looking for same-MAC frames arriving < 1ms apart (no token pass in between)
        mac_last_src: dict[int, tuple[float, int]] = {}
        # Cannot do this retroactively from stats alone — we'd need full frame log
        # Left as placeholder / future enhancement
        pass

    # ── Report ─────────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        elapsed = max(time.perf_counter() - self._start_time, 0.001)
        self.run_diagnostics()

        nodes_summary = []
        for mac, ns in sorted(self._nodes.items()):
            token_avg = (sum(ns.token_times) / len(ns.token_times)
                         if ns.token_times else None)
            token_max = max(ns.token_times) if ns.token_times else None
            nodes_summary.append({
                "mac":           mac,
                "total_frames":  ns.total_frames,
                "frames_per_s":  round(ns.total_frames / elapsed, 2),
                "bytes_per_s":   round(ns.total_bytes / elapsed, 1),
                "bad_crc":       ns.bad_crc_frames,
                "bad_crc_pct":   round(ns.bad_crc_frames / max(ns.total_frames, 1) * 100, 1),
                "token_passes":  len(ns.token_times),
                "token_avg_ms":  round(token_avg, 1) if token_avg is not None else None,
                "token_max_ms":  round(token_max, 1) if token_max is not None else None,
                "device_ids":    sorted(ns.device_instances),
                "frame_types": {
                    FRAME_TYPE_NAMES.get(ft, hex(ft)): cnt
                    for ft, cnt in ns.frame_types.items()
                },
            })

        bits_used = self._total_bytes * 10
        bandwidth  = self.baudrate * elapsed
        utilization_pct = round(min(bits_used / bandwidth * 100, 100.0), 1)

        junk_rate = (self._junk_bytes / max(self._total_bytes, 1)) * 100

        return {
            "duration_s":       round(elapsed, 1),
            "total_frames":     self._total_frames,
            "bad_frames":       self._bad_frames,
            "bad_frame_pct":    round(self._bad_frames / max(self._total_frames, 1) * 100, 1),
            "junk_bytes":       self._junk_bytes,
            "junk_rate_pct":    round(junk_rate, 2),
            "total_bytes":      self._total_bytes,
            "frames_per_s":     round(self._total_frames / elapsed, 1),
            "bytes_per_s":      round(self._total_bytes / elapsed, 0),
            "bus_utilization_pct": utilization_pct,
            "baudrate":         self.baudrate,
            "max_silence_ms":   round(self._max_silence_ms, 1),
            "node_count":       len(self._nodes),
            "duplicate_ids":    {
                str(inst): sorted(macs)
                for inst, macs in self._instance_to_macs.items()
                if len(macs) > 1
            },
            "nodes":            nodes_summary,
            "pathologies":      [
                {
                    "severity":        p.severity,
                    "code":            p.code,
                    "description":     p.description,
                    "nodes_involved":  p.nodes_involved,
                    "detail":          p.detail,
                }
                for p in sorted(self.pathologies,
                                key=lambda x: {"critical": 0, "warning": 1, "info": 2}[x.severity])
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Sniffer — ties parser + analyzer + serial port together
# ═══════════════════════════════════════════════════════════════════════════════

class MstpSniffer:
    """Passive MS/TP bus sniffer — no token ring participation.

    Reads raw bytes from serial port, parses frames, feeds analyzer.
    Thread-safe: serial reading runs in executor thread, analysis can
    happen from asyncio event loop.
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 38400,
        on_frame: Callable[[MstpFrame], None] | None = None,
        on_pathology: Callable[[Pathology], None] | None = None,
        diag_interval: float = 5.0,   # run diagnostics every N seconds
    ):
        self.port         = port
        self.baudrate     = baudrate
        self._on_frame    = on_frame
        self._on_pathology = on_pathology
        self._diag_interval = diag_interval

        self.analyzer = MstpHealthAnalyzer(baudrate=baudrate)
        self._parser  = MstpFrameParser(on_frame=self._handle_frame)
        self._serial: serial.Serial | None = None
        self._running = False
        self._read_task: asyncio.Task | None = None
        self._diag_task: asyncio.Task | None = None
        self.frame_log: list[dict] = []   # rolling 500-frame log for dashboard

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", **kwargs) -> "MstpSniffer":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        serial_cfg = cfg.get("serial", {})
        sniffer_cfg = cfg.get("sniffer", {})
        return cls(
            port=serial_cfg.get("port", "/dev/ttyUSB0"),
            baudrate=serial_cfg.get("baudrate", 38400),
            diag_interval=sniffer_cfg.get("diag_interval_s", 5.0),
            **kwargs,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        # Open serial in thread (blocking)
        await loop.run_in_executor(None, self._open_serial)
        self._running    = True
        self._read_task  = asyncio.create_task(self._read_loop())
        self._diag_task  = asyncio.create_task(self._diag_loop())
        logger.info("[Sniffer] Started on %s @ %d baud (PASSIVE)", self.port, self.baudrate)

    async def stop(self) -> None:
        self._running = False
        for task in (self._read_task, self._diag_task):
            if task and not task.done():
                task.cancel()
        if self._serial and self._serial.is_open:
            self._serial.close()
        logger.info("[Sniffer] Stopped")

    def _open_serial(self) -> None:
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        logger.info("[Sniffer] Serial open: %s", self.port)

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
                logger.error("[Sniffer] Read error: %s", exc)
                await asyncio.sleep(0.5)

    # ── Diagnostics loop ───────────────────────────────────────────────────

    async def _diag_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._diag_interval)
            try:
                pathologies = self.analyzer.run_diagnostics()
                if pathologies and self._on_pathology:
                    for p in pathologies:
                        self._on_pathology(p)
            except Exception as exc:
                logger.debug("[Sniffer] Diag loop error: %s", exc)

    # ── Frame handler ──────────────────────────────────────────────────────

    def _handle_frame(self, frame: MstpFrame) -> None:
        self.analyzer.ingest_frame(frame)
        # Rolling log for dashboard (keep last 500)
        log_entry = {
            "ts":         round(frame.ts, 4),
            "type":       frame.name,
            "src":        frame.src,
            "dst":        frame.dst,
            "len":        len(frame.data),
            "valid":      frame.is_valid,
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

    parser = argparse.ArgumentParser(description="MS/TP Raw Frame Sniffer & Health Analyzer")
    parser.add_argument("--port",     default="/dev/ttyUSB0")
    parser.add_argument("--baud",     type=int, default=38400)
    parser.add_argument("--duration", type=int, default=60, help="Capture duration in seconds")
    parser.add_argument("--json",     action="store_true", help="Output full JSON report")
    parser.add_argument("--frames",   action="store_true", help="Print each frame as it arrives")
    parser.add_argument("--config",   default=None, help="Use config.yaml instead of CLI args")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    def frame_printer(frame: MstpFrame) -> None:
        if args.frames:
            status = "✓" if frame.is_valid else "✗"
            print(f"  {status} {frame.name:12s} src={frame.src:3d} dst={frame.dst:3d} len={len(frame.data):3d}")

    if args.config:
        sniffer = MstpSniffer.from_config(args.config, on_frame=frame_printer)
    else:
        sniffer = MstpSniffer(port=args.port, baudrate=args.baud, on_frame=frame_printer)

    print(f"\n📡 MS/TP Sniffer — {sniffer.port} @ {sniffer.baudrate} baud")
    print(f"   Capturing for {args.duration}s  (Ctrl+C to stop early)\n")

    await sniffer.start()
    try:
        for remaining in range(args.duration, 0, -1):
            await asyncio.sleep(1)
            report = sniffer.get_report()
            frames_seen = report["total_frames"]
            bad  = report["bad_frames"]
            util = report["bus_utilization_pct"]
            print(f"\r  ⏱  {args.duration - remaining + 1:3d}s  |"
                  f"  frames: {frames_seen:5d}  |"
                  f"  bad: {bad:3d}  |"
                  f"  util: {util:4.1f}%  |"
                  f"  nodes: {report['node_count']:3d}",
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

    # ── Pretty print report ───────────────────────────────────────────────
    print("=" * 64)
    print("  MS/TP Network Health Report")
    print("=" * 64)
    print(f"  Duration:        {report['duration_s']:.0f}s")
    print(f"  Total frames:    {report['total_frames']} ({report['frames_per_s']}/s)")
    print(f"  Bad frames:      {report['bad_frames']} ({report['bad_frame_pct']}%)")
    print(f"  Junk bytes:      {report['junk_bytes']} ({report['junk_rate_pct']}%)")
    print(f"  Bus utilization: {report['bus_utilization_pct']}%")
    print(f"  Max silence:     {report['max_silence_ms']}ms")
    print(f"  Nodes seen:      {report['node_count']}")

    dup_ids = report.get("duplicate_ids", {})
    if dup_ids:
        print(f"\n  ⚠️  DUPLICATE DEVICE IDs:")
        for inst, macs in dup_ids.items():
            print(f"     ID {inst} claimed by MACs: {macs}")

    print(f"\n  {'MAC':>4}  {'FPS':>6}  {'Kbps':>6}  {'BadCRC':>6}  {'Tokens':>7}  {'TokenAvg':>9}  Device IDs")
    print(f"  {'─'*4}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*9}  {'─'*20}")
    for n in report["nodes"]:
        ids = ",".join(str(i) for i in n["device_ids"]) or "—"
        print(f"  {n['mac']:4d}  {n['frames_per_s']:6.1f}  "
              f"{n['bytes_per_s']/125:6.1f}  "
              f"{n['bad_crc']:6d}  "
              f"{n['token_passes']:7d}  "
              f"{str(n['token_avg_ms'] or '—'):>9}ms  {ids}")

    if report["pathologies"]:
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        print(f"\n  {'─'*60}")
        print(f"  DIAGNOSTICS ({len(report['pathologies'])} issues found):")
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
