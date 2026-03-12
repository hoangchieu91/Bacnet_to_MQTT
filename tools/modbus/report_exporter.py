"""Auto-Export: Tự động ghi report JSON ra file theo chu kỳ

Dùng cho site KHÔNG CÓ MẠNG — mỗi Pi tự ghi report ra /data/reports/
rồi kỹ thuật viên copy USB về chạy correlator_offline.py

Tích hợp vào dashboard.py hoặc chạy standalone:
  python3 report_exporter.py --config config.yaml --interval 300 --output /data/reports

Tính năng:
  • Ghi report mỗi N giây (mặc định 5 phút)
  • Tên file: {hostname}_{YYYYMMDD_HHMMSS}.json
  • Auto-rotate: giữ tối đa 500 files (~42 giờ nếu 5 phút/file)
  • Ghi kèm metadata: hostname, IP, timestamps, config
  • Hỗ trợ export thủ công qua API: POST /api/export/now
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = "/data/modbus_reports"
DEFAULT_INTERVAL = 300  # 5 minutes
MAX_FILES = 500


class ReportExporter:
    """Periodically export sniffer reports to JSON files."""

    def __init__(
        self,
        get_report: Callable[[], dict],
        export_dir: str = DEFAULT_EXPORT_DIR,
        interval_s: float = DEFAULT_INTERVAL,
        max_files: int = MAX_FILES,
        pi_label: str = "",
    ):
        self.get_report = get_report
        self.export_dir = Path(export_dir)
        self.interval_s = interval_s
        self.max_files = max_files
        self.pi_label = pi_label or platform.node()

        self._running = False
        self._task: asyncio.Task | None = None
        self._export_count = 0
        self._last_export_ts: float | None = None
        self._last_export_path: str | None = None

    async def start(self) -> None:
        """Start the periodic export loop."""
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._export_loop())
        logger.info("[Exporter] Started — writing to %s every %.0fs",
                    self.export_dir, self.interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[Exporter] Stopped — %d reports exported", self._export_count)

    async def _export_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                self.export_now()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[Exporter] Error: %s", exc)

    def export_now(self) -> dict:
        """Export a single report right now. Returns export metadata."""
        try:
            report = self.get_report()
        except Exception as exc:
            logger.error("[Exporter] get_report() failed: %s", exc)
            return {"error": str(exc)}

        # Build export document
        now = datetime.now()
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        hostname = self.pi_label

        doc = {
            "_meta": {
                "version": "1.0",
                "hostname": hostname,
                "pi_label": self.pi_label,
                "ip_addresses": self._get_ips(),
                "export_ts": now.isoformat(),
                "export_ts_unix": time.time(),
                "interval_s": self.interval_s,
                "export_seq": self._export_count + 1,
            },
            "report": report,
        }

        # Write file
        filename = f"{hostname}_{ts_str}.json"
        filepath = self.export_dir / filename
        with open(filepath, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)

        self._export_count += 1
        self._last_export_ts = time.time()
        self._last_export_path = str(filepath)

        logger.info("[Exporter] #%d → %s (%.1f KB)",
                    self._export_count, filename,
                    filepath.stat().st_size / 1024)

        # Rotate old files
        self._rotate()

        return {
            "exported": True,
            "path": str(filepath),
            "size_kb": round(filepath.stat().st_size / 1024, 1),
            "seq": self._export_count,
        }

    def _rotate(self) -> None:
        """Keep only max_files most recent files."""
        files = sorted(self.export_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        while len(files) > self.max_files:
            oldest = files.pop(0)
            oldest.unlink()
            logger.debug("[Exporter] Rotated: %s", oldest.name)

    def get_status(self) -> dict:
        """Get exporter status."""
        files = list(self.export_dir.glob("*.json")) if self.export_dir.exists() else []
        total_size = sum(f.stat().st_size for f in files)
        return {
            "enabled": True,
            "export_dir": str(self.export_dir),
            "interval_s": self.interval_s,
            "export_count": self._export_count,
            "files_on_disk": len(files),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "last_export_ts": self._last_export_ts,
            "last_export_path": self._last_export_path,
            "max_files": self.max_files,
            "pi_label": self.pi_label,
        }

    def list_exports(self, limit: int = 50) -> list[dict]:
        """List recent export files."""
        if not self.export_dir.exists():
            return []
        files = sorted(self.export_dir.glob("*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        return [
            {
                "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
            for f in files[:limit]
        ]

    @staticmethod
    def _get_ips() -> list[str]:
        """Get all IP addresses of this machine."""
        ips = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and ip != "127.0.0.1":
                    ips.append(ip)
        except Exception:
            pass
        return ips


# ═════════════════════════════════════════════════════════════════════════════
# Standalone CLI
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export Modbus RTU reports to JSON")
    parser.add_argument("--config", default="config.yaml", help="Config file")
    parser.add_argument("--interval", type=int, default=300, help="Export interval (seconds)")
    parser.add_argument("--output", default=DEFAULT_EXPORT_DIR, help="Output directory")
    parser.add_argument("--label", default="", help="Pi label (default: hostname)")
    args = parser.parse_args()

    import yaml
    from modbus_sniffer import ModbusSniffer

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    async def main():
        sniffer = ModbusSniffer.from_config(args.config)
        await sniffer.start()

        exporter = ReportExporter(
            get_report=sniffer.get_report,
            export_dir=args.output,
            interval_s=args.interval,
            pi_label=args.label,
        )
        await exporter.start()

        try:
            print(f"\n  📦 Exporting to {args.output} every {args.interval}s")
            print(f"  Press Ctrl+C to stop\n")
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            await exporter.stop()
            await sniffer.stop()

    asyncio.run(main())
