"""Host resource stats, stdlib only. Reads /proc directly rather than
shelling out, so it works the same whether run bare-metal or in a
container (as long as the container isn't run with a restricted /proc)."""

import os
import shutil


def _meminfo():
    raw = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    raw[parts[0].strip()] = int(parts[1].strip().split()[0])
    except OSError:
        pass
    return raw


def get_host_stats():
    load1, load5, load15 = os.getloadavg()
    raw = _meminfo()

    mem = {}
    total_kb = raw.get("MemTotal", 0)
    avail_kb = raw.get("MemAvailable", 0)
    if total_kb:
        mem = {
            "total_gb": round(total_kb / 1048576, 2),
            "available_gb": round(avail_kb / 1048576, 2),
            "used_percent": round((1 - avail_kb / total_kb) * 100, 1),
        }

    swap = {}
    swap_total_kb = raw.get("SwapTotal", 0)
    swap_free_kb = raw.get("SwapFree", 0)
    if swap_total_kb:
        swap = {
            "total_gb": round(swap_total_kb / 1048576, 2),
            "used_gb": round((swap_total_kb - swap_free_kb) / 1048576, 2),
            "used_percent": round((1 - swap_free_kb / swap_total_kb) * 100, 1),
        }

    disk = {}
    try:
        usage = shutil.disk_usage("/")
        disk = {
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb": round(usage.used / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "used_percent": round(usage.used / usage.total * 100, 1),
        }
    except OSError:
        pass

    cpu_temp = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as f:
            cpu_temp = round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        pass

    return {
        "load": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
        "mem": mem,
        "swap": swap,
        "disk": disk,
        "cpu_temp_c": cpu_temp,
    }
