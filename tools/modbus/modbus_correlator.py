"""Dual-Pi Cross-Correlation Engine — Mode 3 Diagnostics

Kết nối 2 Pi (Pi-1 Inline + Pi-2 Passive) để so sánh report
và phát hiện 8 loại bệnh bus chỉ có thể thấy khi có 2 điểm quan sát.

Architecture:
  Correlator chạy trên Pi-1 (dashboard :8766).
  Poll Pi-2's /api/sniffer/report mỗi N giây.
  Cross-compare → phát hiện:
    #16 SIGNAL_DEGRADATION   — CRC % ở Pi-2 > Pi-1
    #17 SEGMENT_FAULT        — Frame mất hoàn toàn
    #18 PROPAGATION_DELAY    — Jitter Δt bất thường
    #19 TERMINATION_FAULT    — CRC pattern theo hướng
    #20 NOISE_LOCALIZATION   — Xác định vùng nhiễu
    #21 FRAME_LOSS           — Tỷ lệ frame mất
    #22 SLAVE_INTERFERENCE   — Slave gây nhiễu
    #23 TIMING_DRIFT         — Sai lệch timing
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CorrelationPathology:
    """A cross-correlation diagnostic result."""
    severity: str       # 'critical' | 'warning' | 'info'
    code: str
    description: str
    detail: str = ""
    pi1_value: str = ""
    pi2_value: str = ""
    slaves_involved: list[int] = field(default_factory=list)


@dataclass
class CorrelationSnapshot:
    """A snapshot of cross-correlation state."""
    ts: float
    pi1_report: dict
    pi2_report: dict
    pathologies: list[CorrelationPathology] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Dual-Pi Correlator
# ═══════════════════════════════════════════════════════════════════════════════

class DualPiCorrelator:
    """Cross-correlate reports from Pi-1 (local) and Pi-2 (remote).

    Pi-1 = Inline proxy (or sniffer) — near Master, knows direction
    Pi-2 = Passive sniffer — at end of bus, worst-case signal quality
    """

    # ── Thresholds ─────────────────────────────────────────────────────────
    SIGNAL_DEG_WARN_PCT   = 5.0    # CRC gap (Pi2-Pi1) > 5% → warning
    SIGNAL_DEG_CRIT_PCT   = 15.0   # CRC gap > 15% → critical
    FRAME_LOSS_WARN_PCT   = 1.0
    FRAME_LOSS_CRIT_PCT   = 5.0
    JUNK_RATIO_THRESHOLD  = 3.0    # junk_Pi2 / junk_Pi1 > 3x → localization
    JITTER_WARN_MS        = 20.0   # Δt jitter > 20ms → warning
    TIMING_DRIFT_THRESH   = 0.1    # ms/s drift rate
    SEGMENT_FAULT_THRESH  = 0.10   # 10% total frames missing → fault

    def __init__(
        self,
        pi2_url: str = "http://10.25.7.22:8766",
        poll_interval: float = 5.0,
        correlation_window_ms: float = 50.0,
    ):
        self.pi2_url = pi2_url.rstrip("/")
        self.poll_interval = poll_interval
        self.correlation_window_ms = correlation_window_ms

        # State
        self._running = False
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._pi2_online = False
        self._pi2_latency_ms: float | None = None
        self._last_pi2_report: dict | None = None
        self._last_correlation: CorrelationSnapshot | None = None

        # History for trend detection
        self._crc_gap_history: list[tuple[float, float]] = []  # (ts, gap_pct)
        self._frame_gap_history: list[tuple[float, float]] = []  # (ts, loss_pct)
        self._jitter_history: list[tuple[float, float]] = []  # (ts, jitter_ms)

        # Callback
        self._on_pathology = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self, get_local_report=None) -> None:
        """Start the correlator background task.

        Args:
            get_local_report: callable that returns the Pi-1 local report dict
        """
        self._get_local_report = get_local_report
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[Correlator] Started — polling Pi-2 at %s every %.0fs",
                    self.pi2_url, self.poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("[Correlator] Stopped")

    # ── Polling ────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[Correlator] Poll error: %s", exc)
                self._pi2_online = False
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Fetch Pi-2 report and run correlation."""
        t0 = time.perf_counter()
        try:
            async with self._session.get(f"{self.pi2_url}/api/sniffer/report") as resp:
                if resp.status == 200:
                    self._last_pi2_report = await resp.json()
                    self._pi2_online = True
                    self._pi2_latency_ms = (time.perf_counter() - t0) * 1000
                else:
                    self._pi2_online = False
                    self._pi2_latency_ms = None
                    return
        except Exception:
            self._pi2_online = False
            self._pi2_latency_ms = None
            return

        # Get local report (Pi-1)
        pi1_report = None
        if self._get_local_report:
            pi1_report = self._get_local_report()

        if pi1_report and self._last_pi2_report:
            self._last_correlation = self._correlate(pi1_report, self._last_pi2_report)

    # ── Cross-Correlation Engine ───────────────────────────────────────────

    def _correlate(self, pi1: dict, pi2: dict) -> CorrelationSnapshot:
        """Run all 8 cross-correlation diagnostics."""
        pathologies: list[CorrelationPathology] = []

        # Minimum data required
        min_frames = 20
        if pi1.get("total_frames", 0) < min_frames or pi2.get("total_frames", 0) < min_frames:
            return CorrelationSnapshot(
                ts=time.time(),
                pi1_report=pi1,
                pi2_report=pi2,
                pathologies=[CorrelationPathology(
                    severity="info", code="INSUFFICIENT_DATA",
                    description=f"Need at least {min_frames} frames on both Pi's",
                    pi1_value=str(pi1.get("total_frames", 0)),
                    pi2_value=str(pi2.get("total_frames", 0)),
                )],
            )

        # Run all checks
        pathologies.extend(self._check_signal_degradation(pi1, pi2))
        pathologies.extend(self._check_segment_fault(pi1, pi2))
        pathologies.extend(self._check_propagation_delay(pi1, pi2))
        pathologies.extend(self._check_termination_fault(pi1, pi2))
        pathologies.extend(self._check_noise_localization(pi1, pi2))
        pathologies.extend(self._check_frame_loss(pi1, pi2))
        pathologies.extend(self._check_slave_interference(pi1, pi2))
        pathologies.extend(self._check_timing_drift(pi1, pi2))

        # Sort by severity
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        pathologies.sort(key=lambda p: sev_order.get(p.severity, 2))

        snap = CorrelationSnapshot(
            ts=time.time(),
            pi1_report=pi1,
            pi2_report=pi2,
            pathologies=pathologies,
        )
        self._last_correlation = snap
        return snap

    # ── Rule 16: SIGNAL_DEGRADATION ────────────────────────────────────────

    def _check_signal_degradation(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        crc1 = pi1.get("bad_frame_pct", 0)
        crc2 = pi2.get("bad_frame_pct", 0)
        gap = crc2 - crc1

        # Track history
        self._crc_gap_history.append((time.time(), gap))
        if len(self._crc_gap_history) > 100:
            self._crc_gap_history = self._crc_gap_history[-100:]

        if gap > self.SIGNAL_DEG_WARN_PCT:
            sev = "critical" if gap > self.SIGNAL_DEG_CRIT_PCT else "warning"
            return [CorrelationPathology(
                severity=sev,
                code="SIGNAL_DEGRADATION",
                description=f"Tín hiệu suy hao: CRC gap = {gap:.1f}% (Pi-2 − Pi-1)",
                detail=f"Pi-2 (cuối bus) có CRC error rate cao hơn Pi-1 (đầu bus) {gap:.1f}%. "
                       f"Nguyên nhân: bus quá dài, cáp kém, hoặc quá nhiều node.",
                pi1_value=f"{crc1:.1f}%",
                pi2_value=f"{crc2:.1f}%",
            )]
        return []

    # ── Rule 17: SEGMENT_FAULT ─────────────────────────────────────────────

    def _check_segment_fault(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        f1 = pi1.get("total_frames", 0)
        f2 = pi2.get("total_frames", 0)

        if f1 == 0:
            return []

        loss_ratio = 1.0 - (f2 / f1) if f1 > f2 else 0

        if loss_ratio > self.SEGMENT_FAULT_THRESH:
            if f2 == 0:
                return [CorrelationPathology(
                    severity="critical",
                    code="SEGMENT_FAULT",
                    description="Pi-2 không nhận được frame nào! Kiểm tra đoạn cáp Pi-1↔Pi-2",
                    detail="Pi-1 forward frames nhưng Pi-2 không thấy gì. "
                           "Có thể đứt dây, hở mạch, hoặc adapter Pi-2 hỏng.",
                    pi1_value=f"{f1} frames",
                    pi2_value="0 frames",
                )]
            return [CorrelationPathology(
                severity="critical",
                code="SEGMENT_FAULT",
                description=f"Mất {loss_ratio*100:.0f}% frame giữa Pi-1 và Pi-2",
                detail=f"Pi-1: {f1} frames, Pi-2: {f2} frames. "
                       f"Đoạn cáp giữa Pi-1 (ComB) và Pi-2 có vấn đề.",
                pi1_value=f"{f1} frames",
                pi2_value=f"{f2} frames",
            )]
        return []

    # ── Rule 18: PROPAGATION_DELAY ─────────────────────────────────────────

    def _check_propagation_delay(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Check timing jitter between two Pis.

        Lý tưởng: mỗi frame Pi-1 gửi → Pi-2 nhận sau Δt cố định.
        Nếu Δt biến động lớn → bus instability.
        """
        # Without frame-level timestamps we can only compare rates
        fps1 = pi1.get("frames_per_s", 0)
        fps2 = pi2.get("frames_per_s", 0)

        if fps1 == 0:
            return []

        # Large difference in frame rate implies timing issues
        fps_diff_pct = abs(fps1 - fps2) / fps1 * 100

        if fps_diff_pct > 20:
            return [CorrelationPathology(
                severity="warning",
                code="PROPAGATION_DELAY",
                description=f"Frame rate chênh lệch {fps_diff_pct:.0f}% giữa 2 Pi",
                detail=f"Pi-1: {fps1:.1f} fps, Pi-2: {fps2:.1f} fps. "
                       f"Có thể do buffer overflow, delay tích lũy, hoặc frame bị drop.",
                pi1_value=f"{fps1:.1f} fps",
                pi2_value=f"{fps2:.1f} fps",
            )]
        return []

    # ── Rule 19: TERMINATION_FAULT ─────────────────────────────────────────

    def _check_termination_fault(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Detect missing termination resistors based on CRC error patterns."""
        crc1 = pi1.get("bad_frame_pct", 0)
        crc2 = pi2.get("bad_frame_pct", 0)

        if crc1 < 3 and crc2 < 3:
            return []  # Both OK, no termination issue

        results = []

        # Case 1: High CRC at master end (Pi-1 high, Pi-2 low)
        if crc1 > 10 and crc2 < 5:
            results.append(CorrelationPathology(
                severity="critical",
                code="TERMINATION_FAULT",
                description="Thiếu termination ĐẦU bus (gần Master)",
                detail=f"CRC errors tập trung ở Pi-1 ({crc1:.1f}%) nhưng Pi-2 OK ({crc2:.1f}%). "
                       f"Tín hiệu phản xạ từ đầu bus chưa terminate. Lắp 120Ω ở đầu Master.",
                pi1_value=f"{crc1:.1f}%",
                pi2_value=f"{crc2:.1f}%",
            ))

        # Case 2: High CRC at slave end (Pi-1 low, Pi-2 high)
        elif crc2 > 10 and crc1 < 5:
            results.append(CorrelationPathology(
                severity="critical",
                code="TERMINATION_FAULT",
                description="Thiếu termination CUỐI bus (gần Slave cuối)",
                detail=f"CRC errors tập trung ở Pi-2 ({crc2:.1f}%) nhưng Pi-1 OK ({crc1:.1f}%). "
                       f"Tín hiệu phản xạ từ cuối bus. Lắp 120Ω ở cuối line.",
                pi1_value=f"{crc1:.1f}%",
                pi2_value=f"{crc2:.1f}%",
            ))

        # Case 3: High CRC at both ends
        elif crc1 > 10 and crc2 > 10:
            results.append(CorrelationPathology(
                severity="critical",
                code="TERMINATION_FAULT",
                description="Thiếu termination CẢ HAI ĐẦU bus",
                detail=f"CRC errors cao ở cả Pi-1 ({crc1:.1f}%) và Pi-2 ({crc2:.1f}%). "
                       f"Lắp 120Ω ở cả đầu Master và cuối Slave.",
                pi1_value=f"{crc1:.1f}%",
                pi2_value=f"{crc2:.1f}%",
            ))

        return results

    # ── Rule 20: NOISE_LOCALIZATION ────────────────────────────────────────

    def _check_noise_localization(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Locate EMI noise source by comparing junk byte rates."""
        junk1 = pi1.get("junk_rate_pct", 0)
        junk2 = pi2.get("junk_rate_pct", 0)

        if junk1 < 1 and junk2 < 1:
            return []     # Both clean

        # Need at least one to be significant
        max_junk = max(junk1, junk2)
        if max_junk < 2:
            return []

        results = []

        if junk1 > 1 and junk2 < 1:
            ratio = junk1 / max(junk2, 0.01)
            results.append(CorrelationPathology(
                severity="warning",
                code="NOISE_LOCALIZATION",
                description=f"Nguồn nhiễu ở Segment 1 (Master → Pi-1)",
                detail=f"Junk bytes Pi-1: {junk1:.1f}%, Pi-2: {junk2:.1f}% "
                       f"(tỷ số {ratio:.0f}x). Kiểm tra VFD/motor/relay gần đoạn Master.",
                pi1_value=f"{junk1:.1f}%",
                pi2_value=f"{junk2:.1f}%",
            ))
        elif junk2 > 1 and junk1 < 1:
            ratio = junk2 / max(junk1, 0.01)
            results.append(CorrelationPathology(
                severity="warning",
                code="NOISE_LOCALIZATION",
                description=f"Nguồn nhiễu ở Segment 2 (Pi-1 → Slave cuối)",
                detail=f"Junk bytes Pi-1: {junk1:.1f}%, Pi-2: {junk2:.1f}% "
                       f"(tỷ số {ratio:.0f}x). Kiểm tra VFD/motor/relay gần đoạn Slave.",
                pi1_value=f"{junk1:.1f}%",
                pi2_value=f"{junk2:.1f}%",
            ))
        elif junk1 > 2 and junk2 > 2:
            results.append(CorrelationPathology(
                severity="warning",
                code="NOISE_LOCALIZATION",
                description="Nhiễu phủ toàn bộ bus hoặc ở giữa",
                detail=f"Junk bytes cao ở cả 2 Pi: Pi-1 {junk1:.1f}%, Pi-2 {junk2:.1f}%. "
                       f"Nhiễu ở giữa bus hoặc nguồn nhiễu mạnh phủ entire line.",
                pi1_value=f"{junk1:.1f}%",
                pi2_value=f"{junk2:.1f}%",
            ))

        return results

    # ── Rule 21: FRAME_LOSS ────────────────────────────────────────────────

    def _check_frame_loss(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Count frame loss rate between Pi-1 and Pi-2."""
        f1 = pi1.get("total_frames", 0)
        f2 = pi2.get("total_frames", 0)

        if f1 == 0:
            return []

        loss_pct = max(0, (f1 - f2) / f1 * 100)

        self._frame_gap_history.append((time.time(), loss_pct))
        if len(self._frame_gap_history) > 100:
            self._frame_gap_history = self._frame_gap_history[-100:]

        if loss_pct > self.FRAME_LOSS_WARN_PCT:
            sev = "critical" if loss_pct > self.FRAME_LOSS_CRIT_PCT else "warning"

            # Per-slave analysis
            detail_parts = []
            slaves1 = {s["slave_id"]: s["total_frames"] for s in pi1.get("slaves", [])}
            slaves2 = {s["slave_id"]: s["total_frames"] for s in pi2.get("slaves", [])}
            worst_slaves = []
            for sid, cnt1 in slaves1.items():
                cnt2 = slaves2.get(sid, 0)
                if cnt1 > 5:
                    slave_loss = max(0, (cnt1 - cnt2) / cnt1 * 100)
                    if slave_loss > 5:
                        detail_parts.append(f"Slave {sid}: {slave_loss:.0f}% loss")
                        worst_slaves.append(sid)

            return [CorrelationPathology(
                severity=sev,
                code="FRAME_LOSS",
                description=f"Frame loss {loss_pct:.1f}% giữa Pi-1 và Pi-2",
                detail=f"Pi-1: {f1}, Pi-2: {f2} frames. " +
                       ("; ".join(detail_parts) if detail_parts else
                        "Kiểm tra chất lượng cáp, adapter Pi-2."),
                pi1_value=f"{f1}",
                pi2_value=f"{f2}",
                slaves_involved=worst_slaves,
            )]
        return []

    # ── Rule 22: SLAVE_INTERFERENCE ────────────────────────────────────────

    def _check_slave_interference(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Detect slaves whose traffic correlates with CRC errors at Pi-2."""
        slaves1 = {s["slave_id"]: s for s in pi1.get("slaves", [])}
        slaves2 = {s["slave_id"]: s for s in pi2.get("slaves", [])}

        results = []
        for sid, s1 in slaves1.items():
            s2 = slaves2.get(sid, {})
            crc1 = s1.get("bad_crc_pct", 0)
            crc2 = s2.get("bad_crc_pct", 0) if isinstance(s2, dict) else 0

            # If a specific slave has high CRC at Pi-2 but not Pi-1
            if crc2 > 15 and crc1 < 5 and s1.get("total_frames", 0) > 10:
                results.append(CorrelationPathology(
                    severity="warning",
                    code="SLAVE_INTERFERENCE",
                    description=f"Slave {sid} gây nhiễu: CRC Pi-2 {crc2:.0f}% vs Pi-1 {crc1:.0f}%",
                    detail=f"Frames từ slave {sid} có CRC errors cao ở cuối bus nhưng OK ở đầu. "
                           f"Driver output yếu hoặc slave ở nhánh rẽ quá dài.",
                    pi1_value=f"{crc1:.1f}%",
                    pi2_value=f"{crc2:.1f}%",
                    slaves_involved=[sid],
                ))

        return results

    # ── Rule 23: TIMING_DRIFT ──────────────────────────────────────────────

    def _check_timing_drift(self, pi1: dict, pi2: dict) -> list[CorrelationPathology]:
        """Detect if timing gap between 2 Pis is growing over time.

        Uses duration difference as proxy — both should report similar uptime.
        """
        dur1 = pi1.get("duration_s", 0)
        dur2 = pi2.get("duration_s", 0)

        if dur1 < 30 or dur2 < 30:
            return []

        # Frame rate drift — if sustained divergence
        fps1 = pi1.get("frames_per_s", 0)
        fps2 = pi2.get("frames_per_s", 0)
        if fps1 == 0:
            return []

        drift_pct = abs(fps1 - fps2) / fps1 * 100

        # Check history for increasing divergence
        if len(self._crc_gap_history) >= 5:
            recent = self._crc_gap_history[-5:]
            old = self._crc_gap_history[:5] if len(self._crc_gap_history) >= 10 else recent
            avg_recent = sum(g for _, g in recent) / len(recent)
            avg_old = sum(g for _, g in old) / len(old)
            trend = avg_recent - avg_old

            if trend > 3.0:  # CRC gap increasing by >3% over observation period
                return [CorrelationPathology(
                    severity="warning",
                    code="TIMING_DRIFT",
                    description=f"Tín hiệu suy giảm theo thời gian (CRC gap tăng {trend:.1f}%)",
                    detail=f"CRC gap ban đầu: {avg_old:.1f}%, hiện tại: {avg_recent:.1f}%. "
                           f"Bus đang degrading — kiểm tra nhiệt độ, mối nối, baud mismatch.",
                    pi1_value=f"{fps1:.1f} fps",
                    pi2_value=f"{fps2:.1f} fps",
                )]

        return []

    # ── Report generation ──────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get Pi-2 connection status."""
        return {
            "enabled": True,
            "pi2_url": self.pi2_url,
            "pi2_online": self._pi2_online,
            "pi2_latency_ms": round(self._pi2_latency_ms, 1) if self._pi2_latency_ms else None,
            "poll_interval_s": self.poll_interval,
            "last_correlation_ts": self._last_correlation.ts if self._last_correlation else None,
        }

    def get_correlation_report(self) -> dict:
        """Get full cross-correlation report."""
        if not self._last_correlation:
            return {
                "available": False,
                "reason": "No correlation data yet" if self._pi2_online
                          else "Pi-2 offline",
                "pi2_online": self._pi2_online,
                "pi2_url": self.pi2_url,
            }

        snap = self._last_correlation
        pi1 = snap.pi1_report
        pi2 = snap.pi2_report

        # Build side-by-side comparison
        comparison = {
            "total_frames":       {"pi1": pi1.get("total_frames", 0),
                                   "pi2": pi2.get("total_frames", 0)},
            "frames_per_s":       {"pi1": pi1.get("frames_per_s", 0),
                                   "pi2": pi2.get("frames_per_s", 0)},
            "bad_frame_pct":      {"pi1": pi1.get("bad_frame_pct", 0),
                                   "pi2": pi2.get("bad_frame_pct", 0)},
            "junk_rate_pct":      {"pi1": pi1.get("junk_rate_pct", 0),
                                   "pi2": pi2.get("junk_rate_pct", 0)},
            "bus_utilization_pct":{"pi1": pi1.get("bus_utilization_pct", 0),
                                   "pi2": pi2.get("bus_utilization_pct", 0)},
            "slave_count":        {"pi1": pi1.get("slave_count", 0),
                                   "pi2": pi2.get("slave_count", 0)},
            "duration_s":         {"pi1": pi1.get("duration_s", 0),
                                   "pi2": pi2.get("duration_s", 0)},
        }

        # Per-slave loss matrix
        slaves1 = {s["slave_id"]: s for s in pi1.get("slaves", [])}
        slaves2 = {s["slave_id"]: s for s in pi2.get("slaves", [])}
        all_sids = sorted(set(slaves1.keys()) | set(slaves2.keys()))
        slave_matrix = []
        for sid in all_sids:
            s1 = slaves1.get(sid, {})
            s2 = slaves2.get(sid, {})
            f1 = s1.get("total_frames", 0)
            f2 = s2.get("total_frames", 0)
            loss = max(0, (f1 - f2) / f1 * 100) if f1 > 0 else 0
            slave_matrix.append({
                "slave_id": sid,
                "frames_pi1": f1,
                "frames_pi2": f2,
                "loss_pct": round(loss, 1),
                "crc_pi1": s1.get("bad_crc_pct", 0),
                "crc_pi2": s2.get("bad_crc_pct", 0) if isinstance(s2, dict) else 0,
            })

        return {
            "available": True,
            "ts": snap.ts,
            "pi2_online": self._pi2_online,
            "pi2_url": self.pi2_url,
            "pi2_latency_ms": round(self._pi2_latency_ms, 1) if self._pi2_latency_ms else None,
            "comparison": comparison,
            "slave_matrix": slave_matrix,
            "pathologies": [
                {
                    "severity":     p.severity,
                    "code":         p.code,
                    "description":  p.description,
                    "detail":       p.detail,
                    "pi1_value":    p.pi1_value,
                    "pi2_value":    p.pi2_value,
                    "slaves_involved": p.slaves_involved,
                }
                for p in snap.pathologies
            ],
            "crc_gap_trend": [
                {"ts": ts, "gap": round(g, 2)}
                for ts, g in self._crc_gap_history[-30:]
            ],
            "frame_loss_trend": [
                {"ts": ts, "loss": round(l, 2)}
                for ts, l in self._frame_gap_history[-30:]
            ],
        }
