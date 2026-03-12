#!/usr/bin/env python3
"""Offline Dual-Pi Correlator CLI

Chạy cross-correlation trên laptop KHÔNG CẦN MẠNG.
Input: 2 file JSON (từ Pi-1 và Pi-2, export bởi report_exporter.py)
Output: HTML report hoặc terminal output

Usage:
  # Từ 2 file đơn lẻ:
  python3 correlator_offline.py pi1_report.json pi2_report.json

  # Từ 2 thư mục export:
  python3 correlator_offline.py /usb/pi1_reports/ /usb/pi2_reports/

  # Xuất HTML:
  python3 correlator_offline.py pi1.json pi2.json --html result.html

  # Batch mode (nhiều cặp reports):
  python3 correlator_offline.py /usb/pi1/ /usb/pi2/ --batch --html batch_result.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Import correlator engine ──────────────────────────────────────────────────
# Add parent dir to path so we can import modbus_correlator
sys.path.insert(0, str(Path(__file__).parent))
from modbus_correlator import DualPiCorrelator, CorrelationPathology


# ═════════════════════════════════════════════════════════════════════════════
# File loading
# ═════════════════════════════════════════════════════════════════════════════

def load_report(path: str) -> dict:
    """Load a report from a JSON file. Handles both raw reports and meta-wrapped."""
    with open(path) as f:
        data = json.load(f)
    # If wrapped by report_exporter.py  →  {"_meta": {...}, "report": {...}}
    if "_meta" in data and "report" in data:
        return data["report"], data["_meta"]
    return data, {"hostname": "unknown", "export_ts": "unknown"}


def load_reports_from_dir(dirpath: str) -> list[tuple[dict, dict]]:
    """Load all JSON reports from a directory, sorted by timestamp."""
    reports = []
    p = Path(dirpath)
    for f in sorted(p.glob("*.json")):
        try:
            report, meta = load_report(str(f))
            reports.append((report, meta, str(f)))
        except Exception as e:
            print(f"  ⚠️  Skip {f.name}: {e}", file=sys.stderr)
    return reports


def match_report_pairs(dir1_reports, dir2_reports, window_s: float = 60):
    """Match reports by closest timestamp."""
    pairs = []
    for r1, m1, f1 in dir1_reports:
        ts1 = m1.get("export_ts_unix", 0)
        best = None
        best_dt = float("inf")
        for r2, m2, f2 in dir2_reports:
            ts2 = m2.get("export_ts_unix", 0)
            dt = abs(ts1 - ts2)
            if dt < best_dt and dt < window_s:
                best_dt = dt
                best = (r2, m2, f2)
        if best:
            pairs.append((r1, m1, f1, best[0], best[1], best[2], best_dt))
    return pairs


# ═════════════════════════════════════════════════════════════════════════════
# Terminal output
# ═════════════════════════════════════════════════════════════════════════════

SEV_ICONS = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
SEV_COLORS = {"critical": "\033[91m", "warning": "\033[93m", "info": "\033[94m"}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def print_comparison(report: dict) -> None:
    """Print correlation report to terminal."""
    cmp = report.get("comparison", {})

    print(f"\n{BOLD}{'━' * 70}{RESET}")
    print(f"{BOLD}  📊 DUAL-PI CROSS-CORRELATION REPORT{RESET}")
    print(f"{'━' * 70}\n")

    # Side-by-side
    print(f"  {'Metric':<25} {'Pi-1 (Inline)':<20} {'Pi-2 (Passive)':<20}")
    print(f"  {'─' * 65}")
    for key, label in [
        ("total_frames", "Total frames"),
        ("frames_per_s", "Frames/s"),
        ("bad_frame_pct", "CRC error %"),
        ("junk_rate_pct", "Junk rate %"),
        ("bus_utilization_pct", "Bus utilization %"),
        ("slave_count", "Slaves"),
        ("duration_s", "Duration (s)"),
    ]:
        v1 = cmp.get(key, {}).get("pi1", "—")
        v2 = cmp.get(key, {}).get("pi2", "—")
        # Highlight CRC difference
        color = ""
        if key == "bad_frame_pct" and isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            gap = v2 - v1
            if gap > 15:
                color = SEV_COLORS["critical"]
            elif gap > 5:
                color = SEV_COLORS["warning"]
        print(f"  {label:<25} {str(v1):<20} {color}{str(v2):<20}{RESET if color else ''}")

    # Pathologies
    pathos = report.get("pathologies", [])
    print(f"\n{BOLD}  🔍 CROSS-CORRELATION DIAGNOSTICS ({len(pathos)} issues){RESET}")
    print(f"  {'─' * 65}")

    if not pathos:
        print(f"  ✅ No issues detected")
    else:
        for p in pathos:
            icon = SEV_ICONS.get(p["severity"], "•")
            color = SEV_COLORS.get(p["severity"], "")
            print(f"\n  {icon} {color}{BOLD}{p['code']}{RESET}")
            print(f"     {p['description']}")
            if p.get("detail"):
                print(f"     {DIM}{p['detail']}{RESET}")
            if p.get("pi1_value") or p.get("pi2_value"):
                print(f"     Pi-1: {p.get('pi1_value', '—')}  |  Pi-2: {p.get('pi2_value', '—')}")

    # Slave matrix
    matrix = report.get("slave_matrix", [])
    if matrix:
        print(f"\n{BOLD}  📋 FRAME LOSS PER SLAVE{RESET}")
        print(f"  {'─' * 65}")
        print(f"  {'Slave':>6}  {'Frames Pi-1':>12}  {'Frames Pi-2':>12}  {'Loss%':>8}  {'CRC Pi-1':>9}  {'CRC Pi-2':>9}")
        for s in matrix:
            loss_c = SEV_COLORS["critical"] if s["loss_pct"] > 5 else (SEV_COLORS["warning"] if s["loss_pct"] > 1 else "")
            print(f"  {s['slave_id']:>6}  {s['frames_pi1']:>12}  {s['frames_pi2']:>12}  "
                  f"{loss_c}{s['loss_pct']:>7.1f}%{RESET if loss_c else ''}  "
                  f"{s.get('crc_pi1', 0):>8.1f}%  {s.get('crc_pi2', 0):>8.1f}%")

    print(f"\n{'━' * 70}\n")


# ═════════════════════════════════════════════════════════════════════════════
# HTML output
# ═════════════════════════════════════════════════════════════════════════════

def generate_html(report: dict, meta1: dict = None, meta2: dict = None) -> str:
    """Generate a standalone HTML report."""
    cmp = report.get("comparison", {})
    pathos = report.get("pathologies", [])
    matrix = report.get("slave_matrix", [])

    sev_bg = {"critical": "#fca5a5", "warning": "#fde68a", "info": "#93c5fd"}

    # Build comparison rows
    cmp_rows = ""
    for key, label in [
        ("total_frames", "Total frames"),
        ("frames_per_s", "Frames/s"),
        ("bad_frame_pct", "CRC error %"),
        ("junk_rate_pct", "Junk rate %"),
        ("bus_utilization_pct", "Bus util %"),
        ("slave_count", "Slaves"),
        ("duration_s", "Duration (s)"),
    ]:
        v1 = cmp.get(key, {}).get("pi1", "—")
        v2 = cmp.get(key, {}).get("pi2", "—")
        cmp_rows += f"<tr><td>{label}</td><td>{v1}</td><td>{v2}</td></tr>\n"

    # Build pathology cards
    patho_html = ""
    if not pathos:
        patho_html = '<p style="text-align:center;color:#666;padding:20px;">✅ No issues detected</p>'
    else:
        for p in pathos:
            bg = sev_bg.get(p["severity"], "#e5e7eb")
            icon = SEV_ICONS.get(p["severity"], "•")
            patho_html += f"""
            <div style="border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:10px;
                        border-left:4px solid {bg};">
              <div><span style="font-size:14px;">{icon}</span>
                   <code style="font-weight:700;background:#f3f4f6;padding:2px 8px;border-radius:4px;">{p['code']}</code></div>
              <div style="margin-top:6px;font-size:14px;">{p['description']}</div>
              {"<div style='margin-top:4px;font-size:12px;color:#6b7280;'>" + p['detail'] + "</div>" if p.get('detail') else ""}
              {"<div style='margin-top:6px;font-size:12px;'><span style=\"background:#dbeafe;padding:2px 8px;border-radius:4px;\">Pi-1: " + str(p.get('pi1_value','')) + "</span> <span style=\"background:#ede9fe;padding:2px 8px;border-radius:4px;\">Pi-2: " + str(p.get('pi2_value','')) + "</span></div>" if p.get('pi1_value') else ""}
            </div>"""

    # Build slave matrix
    matrix_rows = ""
    for s in matrix:
        loss_bg = "#fca5a5" if s["loss_pct"] > 5 else ("#fde68a" if s["loss_pct"] > 1 else "")
        matrix_rows += f"""<tr>
          <td><b>{s['slave_id']}</b></td>
          <td>{s['frames_pi1']}</td><td>{s['frames_pi2']}</td>
          <td style="font-weight:700;{'background:'+loss_bg+';' if loss_bg else ''}">{s['loss_pct']}%</td>
          <td>{s.get('crc_pi1', 0)}%</td><td>{s.get('crc_pi2', 0)}%</td>
        </tr>"""

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m1_label = meta1.get("hostname", "Pi-1") if meta1 else "Pi-1"
    m2_label = meta2.get("hostname", "Pi-2") if meta2 else "Pi-2"

    crc1 = cmp.get("bad_frame_pct", {}).get("pi1", 0)
    crc2 = cmp.get("bad_frame_pct", {}).get("pi2", 0)
    q1 = max(0, 100 - (crc1 if isinstance(crc1, (int, float)) else 0) * 5)
    q2 = max(0, 100 - (crc2 if isinstance(crc2, (int, float)) else 0) * 5)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Dual-Pi Correlation Report</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#fafafa;color:#1a1a1a;}}
  h1{{font-size:1.5rem;border-bottom:2px solid #f59e0b;padding-bottom:8px;}}
  h2{{font-size:1.1rem;margin-top:24px;color:#374151;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0;}}
  th{{background:#f3f4f6;text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;}}
  td{{padding:7px 10px;border-top:1px solid #e5e7eb;}}
  tr:hover td{{background:#f9fafb;}}
  .meta{{font-size:11px;color:#6b7280;margin-bottom:20px;}}
  .sig-bar{{height:16px;border-radius:4px;margin:4px 0;}}
  .sig-label{{font-size:11px;color:#6b7280;text-align:right;}}
  code{{background:#f3f4f6;padding:1px 6px;border-radius:3px;font-size:12px;}}
  .topology{{background:#1e293b;color:#94a3b8;padding:16px;border-radius:8px;font-family:monospace;font-size:12px;line-height:1.6;white-space:pre;overflow-x:auto;}}
</style>
</head>
<body>
<h1>📊 Dual-Pi Cross-Correlation Report</h1>
<div class="meta">
  Generated: {now}<br>
  Pi-1: <b>{m1_label}</b> (Inline) &nbsp;|&nbsp; Pi-2: <b>{m2_label}</b> (Passive)<br>
  {f"Pi-1 exported: {meta1.get('export_ts', '—')}" if meta1 else ""}
  {f" &nbsp;|&nbsp; Pi-2 exported: {meta2.get('export_ts', '—')}" if meta2 else ""}
</div>

<h2>📶 Signal Quality</h2>
<div style="font-size:11px;color:#6b7280;display:flex;justify-content:space-between;">
  <span>🟦 Pi-1 (Đầu bus)</span><span>🟪 Pi-2 (Cuối bus)</span>
</div>
<div style="background:#e5e7eb;border-radius:4px;height:18px;overflow:hidden;">
  <div style="height:100%;width:{q1}%;background:linear-gradient(90deg,#22c55e,#3b82f6);border-radius:4px;"></div>
</div>
<div class="sig-label">CRC: {crc1}% — Signal: {q1:.0f}%</div>
<div style="background:#e5e7eb;border-radius:4px;height:18px;overflow:hidden;margin-top:4px;">
  <div style="height:100%;width:{q2}%;background:linear-gradient(90deg,#22c55e,#a855f7);border-radius:4px;"></div>
</div>
<div class="sig-label">CRC: {crc2}% — Signal: {q2:.0f}%</div>

<h2>📋 Side-by-Side Comparison</h2>
<table>
  <tr><th>Metric</th><th>Pi-1 (Inline)</th><th>Pi-2 (Passive)</th></tr>
  {cmp_rows}
</table>

<h2>🔍 Cross-Correlation Diagnostics ({len(pathos)} issues)</h2>
{patho_html}

<h2>📊 Frame Loss per Slave</h2>
<table>
  <tr><th>Slave</th><th>Frames Pi-1</th><th>Frames Pi-2</th><th>Loss %</th><th>CRC Pi-1</th><th>CRC Pi-2</th></tr>
  {matrix_rows if matrix_rows else "<tr><td colspan='6' style='text-align:center;color:#999;'>No slave data</td></tr>"}
</table>

<h2>🖧 Topology</h2>
<div class="topology">
 ┌────────┐    ┌─[Pi-1 Inline]──┐    ┌────┐ ┌────┐    ┌─[Pi-2]─┐
 │ MASTER ├─A──┤ComA        ComB├─A──┤ S1 ├─┤ S2 ├─A──┤Passive │
 │ PLC    ├─B──┤                ├─B──┤    ├─┤    ├─B──┤Sniffer │
 └────────┘    └────────────────┘    └────┘ └────┘    └────────┘
 [120Ω]        segment 1 │ segment 2                   [120Ω]
                         │
                 Pi-1 cắt bus ở đây, forward A⇄B
</div>

<div style="margin-top:20px;font-size:11px;color:#9ca3af;text-align:center;">
  Modbus RTU Dual-Pi Diagnostic Tool — Offline Analysis
</div>
</body></html>"""


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Offline Dual-Pi Correlator — cross-compare reports from 2 Pi's",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s pi1_report.json pi2_report.json
  %(prog)s /usb/pi1_reports/ /usb/pi2_reports/ --batch
  %(prog)s pi1.json pi2.json --html result.html
        """,
    )
    parser.add_argument("pi1", help="Pi-1 report file or directory")
    parser.add_argument("pi2", help="Pi-2 report file or directory")
    parser.add_argument("--html", metavar="FILE", help="Generate HTML report")
    parser.add_argument("--batch", action="store_true",
                        help="Process all matched report pairs from directories")
    parser.add_argument("--window", type=float, default=60,
                        help="Time window for matching reports (seconds, default: 60)")
    args = parser.parse_args()

    # Single file mode
    if os.path.isfile(args.pi1) and os.path.isfile(args.pi2):
        report1, meta1 = load_report(args.pi1)
        report2, meta2 = load_report(args.pi2)

        correlator = DualPiCorrelator.__new__(DualPiCorrelator)
        correlator._crc_gap_history = []
        correlator._frame_gap_history = []
        correlator._jitter_history = []
        correlator.SIGNAL_DEG_WARN_PCT = 5.0
        correlator.SIGNAL_DEG_CRIT_PCT = 15.0
        correlator.FRAME_LOSS_WARN_PCT = 1.0
        correlator.FRAME_LOSS_CRIT_PCT = 5.0
        correlator.JUNK_RATIO_THRESHOLD = 3.0
        correlator.JITTER_WARN_MS = 20.0
        correlator.TIMING_DRIFT_THRESH = 0.1
        correlator.SEGMENT_FAULT_THRESH = 0.10
        correlator.pi2_url = "offline"
        correlator._pi2_online = True
        correlator._pi2_latency_ms = None
        correlator._last_correlation = None

        snap = correlator._correlate(report1, report2)
        result = correlator.get_correlation_report()

        print_comparison(result)

        if args.html:
            html = generate_html(result, meta1, meta2)
            with open(args.html, "w") as f:
                f.write(html)
            print(f"  📄 HTML report saved: {args.html}")

    # Batch directory mode
    elif os.path.isdir(args.pi1) and os.path.isdir(args.pi2):
        print(f"  📂 Loading reports from directories...")
        dir1 = load_reports_from_dir(args.pi1)
        dir2 = load_reports_from_dir(args.pi2)
        print(f"     Pi-1: {len(dir1)} reports from {args.pi1}")
        print(f"     Pi-2: {len(dir2)} reports from {args.pi2}")

        pairs = match_report_pairs(dir1, dir2, args.window)
        print(f"     Matched {len(pairs)} pairs (window={args.window}s)\n")

        if not pairs:
            print("  ❌ No matching report pairs found")
            sys.exit(1)

        all_results = []
        for i, (r1, m1, f1, r2, m2, f2, dt) in enumerate(pairs):
            correlator = DualPiCorrelator.__new__(DualPiCorrelator)
            correlator._crc_gap_history = []
            correlator._frame_gap_history = []
            correlator._jitter_history = []
            correlator.SIGNAL_DEG_WARN_PCT = 5.0
            correlator.SIGNAL_DEG_CRIT_PCT = 15.0
            correlator.FRAME_LOSS_WARN_PCT = 1.0
            correlator.FRAME_LOSS_CRIT_PCT = 5.0
            correlator.JUNK_RATIO_THRESHOLD = 3.0
            correlator.JITTER_WARN_MS = 20.0
            correlator.TIMING_DRIFT_THRESH = 0.1
            correlator.SEGMENT_FAULT_THRESH = 0.10
            correlator.pi2_url = "offline"
            correlator._pi2_online = True
            correlator._pi2_latency_ms = None
            correlator._last_correlation = None

            snap = correlator._correlate(r1, r2)
            result = correlator.get_correlation_report()
            all_results.append((result, m1, m2, dt))

            n_issues = len(result.get("pathologies", []))
            ts = m1.get("export_ts", "?")
            status = "✅" if n_issues == 0 else f"⚠️  {n_issues} issues"
            print(f"  [{i+1}/{len(pairs)}] {ts}  Δt={dt:.0f}s  {status}")

        # Summary
        total_issues = sum(len(r.get("pathologies", [])) for r, *_ in all_results)
        print(f"\n  {'━' * 50}")
        print(f"  📊 Batch summary: {len(pairs)} pairs, {total_issues} total issues")

        if args.html:
            # Use the latest result for HTML
            latest = all_results[-1]
            html = generate_html(latest[0], latest[1], latest[2])
            with open(args.html, "w") as f:
                f.write(html)
            print(f"  📄 HTML report (latest): {args.html}")
    else:
        print("  ❌ Both arguments must be files or both must be directories")
        sys.exit(1)


if __name__ == "__main__":
    main()
