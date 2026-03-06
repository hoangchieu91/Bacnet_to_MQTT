"""Pi Health Monitor — reads system metrics (CPU, RAM, Disk, Temperature)."""

from __future__ import annotations

import os
import time
import logging

logger = logging.getLogger(__name__)

# Thresholds (matching gateway_engine.py)
RAM_WARN_PCT = 80
RAM_THROTTLE_PCT = 90
RAM_CRITICAL_PCT = 95


def get_system_health() -> dict:
    """Return a dict with CPU, RAM, Disk, Temperature metrics."""
    result = {
        "cpu_percent": _get_cpu_percent(),
        "cpu_temp": _get_cpu_temp(),
        "ram_used_mb": 0,
        "ram_total_mb": 0,
        "ram_available_mb": 0,
        "ram_percent": 0.0,
        "ram_status": "normal",
        "disk_used_gb": 0.0,
        "disk_total_gb": 0.0,
        "disk_percent": 0.0,
        "load_avg_1m": 0.0,
        "load_avg_5m": 0.0,
        "load_avg_15m": 0.0,
    }

    # RAM
    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])  # kB

        total_kb = mem.get("MemTotal", 1)
        available_kb = mem.get("MemAvailable", total_kb)
        used_kb = total_kb - available_kb
        pct = (used_kb / total_kb) * 100

        result["ram_total_mb"] = round(total_kb / 1024)
        result["ram_used_mb"] = round(used_kb / 1024)
        result["ram_available_mb"] = round(available_kb / 1024)
        result["ram_percent"] = round(pct, 1)

        if pct >= RAM_CRITICAL_PCT:
            result["ram_status"] = "critical"
        elif pct >= RAM_THROTTLE_PCT:
            result["ram_status"] = "throttle"
        elif pct >= RAM_WARN_PCT:
            result["ram_status"] = "warn"
        else:
            result["ram_status"] = "normal"
    except Exception as e:
        logger.warning("RAM read error: %s", e)

    # Disk
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        result["disk_total_gb"] = round(total / (1024**3), 1)
        result["disk_used_gb"] = round(used / (1024**3), 1)
        result["disk_percent"] = round((used / total) * 100, 1) if total > 0 else 0
    except Exception as e:
        logger.warning("Disk read error: %s", e)

    # Load average
    try:
        load1, load5, load15 = os.getloadavg()
        result["load_avg_1m"] = round(load1, 2)
        result["load_avg_5m"] = round(load5, 2)
        result["load_avg_15m"] = round(load15, 2)
    except Exception:
        pass

    return result


# CPU usage cache for delta calculation
_prev_cpu: dict | None = None
_prev_time: float = 0


def _get_cpu_percent() -> float:
    """Read CPU usage from /proc/stat (delta between two reads)."""
    global _prev_cpu, _prev_time
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        # user, nice, system, idle, iowait, irq, softirq, steal
        vals = [int(x) for x in parts[1:9]]
        total = sum(vals)
        idle = vals[3] + vals[4]  # idle + iowait

        if _prev_cpu is None:
            _prev_cpu = {"total": total, "idle": idle}
            _prev_time = time.time()
            return 0.0

        d_total = total - _prev_cpu["total"]
        d_idle = idle - _prev_cpu["idle"]
        _prev_cpu = {"total": total, "idle": idle}
        _prev_time = time.time()

        if d_total == 0:
            return 0.0
        return round(((d_total - d_idle) / d_total) * 100, 1)
    except Exception:
        return 0.0


def _get_cpu_temp() -> float | None:
    """Read CPU temperature from thermal zone."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def check_ram_for_new_points(count: int = 1) -> dict:
    """Check if RAM is healthy enough to add new points.

    Returns:
        dict with 'allowed', 'warning', 'ram_percent', 'ram_status'
    """
    health = get_system_health()
    pct = health["ram_percent"]
    status = health["ram_status"]

    if status == "critical":
        return {
            "allowed": False,
            "warning": f"⛔ RAM quá tải ({pct:.0f}%)! Không thể thêm points. Hãy giảm bớt mappings hoặc chuyển sang COV mode.",
            "ram_percent": pct,
            "ram_status": status,
        }
    elif status == "throttle":
        return {
            "allowed": True,
            "warning": f"⚠️ RAM cao ({pct:.0f}%)! Polling đang bị giảm tốc x2. Nên dùng COV mode hoặc giảm bớt points.",
            "ram_percent": pct,
            "ram_status": status,
        }
    elif status == "warn":
        return {
            "allowed": True,
            "warning": f"🟡 RAM đang ở mức cảnh báo ({pct:.0f}%). Cân nhắc số lượng points thêm vào.",
            "ram_percent": pct,
            "ram_status": status,
        }
    else:
        return {
            "allowed": True,
            "warning": None,
            "ram_percent": pct,
            "ram_status": status,
        }
