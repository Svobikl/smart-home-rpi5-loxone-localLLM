#!/usr/bin/env python3
"""Heartbeat health check for Hermes Agent & MySmartHome Smart Home on Raspberry Pi 5."""
import os
import sys
import json
import time
import shutil
import socket
from pathlib import Path

HEARTBEAT_LOG = Path.home() / ".hermes" / "logs" / "heartbeat_status.json"

def get_cpu_temp_c() -> float:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    if p.exists():
        try:
            return round(int(p.read_text().strip()) / 1000.0, 1)
        except Exception:
            pass
    return 0.0

def get_mem_info() -> dict:
    info = {}
    p = Path("/proc/meminfo")
    if p.exists():
        for line in p.read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.strip().split()[0])
    total_mb = round(info.get("MemTotal", 0) / 1024.0, 1)
    avail_mb = round(info.get("MemAvailable", 0) / 1024.0, 1)
    return {"total_mb": total_mb, "available_mb": avail_mb, "used_pct": round((1 - avail_mb / max(total_mb, 1)) * 100, 1)}

def tcp_ping(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def main():
    t0 = time.perf_counter()
    disk = shutil.disk_usage(str(Path.home()))
    services = {
        "home_assistant_8123": tcp_ping("localhost", 8123),
        "matter_server_5580": tcp_ping("localhost", 5580),
        "deye_fve_15kw": tcp_ping(os.environ.get("DEYE_IP", "<DEYE_INVERTER_HOST>"), 80),
        "loxone_miniserver": tcp_ping(os.environ.get("LOXONE_IP", "<LOXONE_MINISERVER_HOST>"), 80),
    }
    cpu_temp = get_cpu_temp_c()
    mem = get_mem_info()
    status = {
        "ok": services["home_assistant_8123"] and cpu_temp < 82.0 and mem["used_pct"] < 95.0,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rpi5": {
            "cpu_temp_c": cpu_temp,
            "memory": mem,
            "disk_free_gb": round(disk.free / (1024**3), 2),
        },
        "services": services,
        "hermes_env_secure": (Path.home() / ".hermes" / ".env").exists(),
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    HEARTBEAT_LOG.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_LOG.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
