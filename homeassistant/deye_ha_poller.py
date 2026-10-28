#!/usr/bin/env python3
"""Home Assistant Solarman V5 Modbus Poller for Deye SUN-15K-SG05LP3-EU-SM2 & Eastron SDM630MCT V2."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

sys.stdout.reconfigure(encoding="utf-8")

DEYE_IP = os.environ.get("DEYE_IP", "<DEYE_INVERTER_HOST>")
DEYE_PORT = 8899
LOGGER_SN = int(os.environ.get("DEYE_LOGGER_SN", "1234567890"))
SLAVE_ID = 1
CACHE_FILE = Path(tempfile.gettempdir()) / "deye_ha_last_good.json"


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_v5_read(start_reg: int, count: int, seq: int = 1) -> bytes:
    mb = struct.pack(">BBHH", SLAVE_ID, 0x03, start_reg, count)
    mb += struct.pack("<H", modbus_crc16(mb))
    bf = b"\x02\x00\x00" + b"\x00" * 12 + mb
    hdr = (
        b"\xa5"
        + struct.pack("<H", len(bf))
        + struct.pack("<H", 0x4510)
        + struct.pack("<H", seq & 0xFFFF)
        + struct.pack("<I", LOGGER_SN)
    )
    chk = sum(hdr[1:] + bf) & 0xFF
    return hdr + bf + struct.pack("B", chk) + b"\x15"


def parse_v5_read(raw: bytes, count: int) -> List[int]:
    bc = count * 2
    mlen = 3 + bc + 2
    for idx in range(20, len(raw) - mlen + 1):
        if raw[idx] == SLAVE_ID and raw[idx + 1] == 0x03 and raw[idx + 2] == bc:
            cand = raw[idx : idx + mlen]
            if modbus_crc16(cand[:-2]) == struct.unpack("<H", cand[-2:])[0]:
                db = cand[3:-2]
                return [struct.unpack(">H", db[i * 2 : (i + 1) * 2])[0] for i in range(count)]
    raise ValueError("CRC or frame mismatch")


def read_block(start_reg: int, count: int, seq: int = 1) -> List[int]:
    for attempt in range(3):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.5)
                s.connect((DEYE_IP, DEYE_PORT))
                s.sendall(build_v5_read(start_reg, count, seq=seq + attempt))
                raw = s.recv(2048)
            return parse_v5_read(raw, count)
        except Exception:
            time.sleep(0.3 * (attempt + 1))
    raise RuntimeError(f"Failed block {start_reg}..{start_reg+count-1}")


def s16(val: int) -> int:
    return val - 0x10000 if val >= 0x8000 else val


def u32(low: int, high: int) -> int:
    return (high << 16) | low


def poll_deye() -> Dict[str, object]:
    b500 = read_block(500, 60, seq=10)
    time.sleep(0.12)
    b580 = read_block(580, 90, seq=20)
    time.sleep(0.12)
    try:
        b670 = read_block(670, 10, seq=30)
    except Exception:
        b670 = [0] * 10

    def r(addr: int) -> int:
        if 500 <= addr < 560:
            return b500[addr - 500]
        if 580 <= addr < 670:
            return b580[addr - 580]
        if 670 <= addr < 680:
            return b670[addr - 670]
        return 0

    run_code = r(500)
    fault1 = r(555)
    run_map = {
        0: "Pohotovost (Standby)",
        1: "Autotest (Self-check)",
        2: "V provozu (Normal)",
        3: "Varování (Alarm)",
        4: "Přechod režimu F13 (Čeká na FV)" if fault1 == 0x1000 else f"Porucha (0x{fault1:04X})",
    }
    relay = r(552)
    dc_temp = round((r(540) - 1000) * 0.1, 1) if r(540) > 500 else 0.0
    hs_temp = round((r(541) - 1000) * 0.1, 1) if r(541) > 500 else 0.0

    pv1_w = r(672)
    pv2_w = r(673)
    pv_total_w = pv1_w + pv2_w

    eastron_l1 = s16(r(616))
    eastron_l2 = s16(r(617))
    eastron_l3 = s16(r(618))
    eastron_total = s16(r(619))

    load_l1 = s16(r(650))
    load_l2 = s16(r(651))
    load_l3 = s16(r(652))
    load_total = s16(r(653))

    # Note: If CT clamp on L3 is physically reversed (reading negative during grid import),
    # compute corrected per-phase and total house load so dashboard always displays true consumption.
    l1_corr = abs(eastron_l1) if pv_total_w == 0 and eastron_l1 < 0 else eastron_l1
    l2_corr = abs(eastron_l2) if pv_total_w == 0 and eastron_l2 < 0 else eastron_l2
    l3_corr = abs(eastron_l3) if pv_total_w == 0 and eastron_l3 < 0 else eastron_l3
    grid_net_w = l1_corr + l2_corr + l3_corr
    house_total_corr = max(0, pv_total_w + grid_net_w)

    if grid_net_w > 20:
        grid_flow_state = "Odběr ze sítě"
    elif grid_net_w < -20:
        grid_flow_state = "Přetok do sítě"
    else:
        grid_flow_state = "Nulový odběr (Zero Export)"

    # Battery telemetry (registers 586..591 on Deye SG05LP3)
    raw_bat_v = round(r(587) * 0.01, 1)
    raw_bat_soc = int(r(588))
    raw_bat_p = s16(r(590))
    raw_bat_t = round((r(586) - 1000) * 0.1, 1) if r(586) > 500 else 0.0

    # When battery is not physically installed (voltage < 15V or SOC > 100), report 0% Ready state
    battery_connected = raw_bat_v >= 15.0 and 0 < raw_bat_soc <= 100
    if battery_connected:
        bat_soc = raw_bat_soc
        bat_power_w = raw_bat_p
        bat_voltage_v = raw_bat_v
        bat_temp_c = raw_bat_t
        if bat_power_w < -20:
            bat_status = f"Nabíjení ({abs(bat_power_w)} W)"
        elif bat_power_w > 20:
            bat_status = f"Vybíjení ({bat_power_w} W)"
        else:
            bat_status = "Pohotovost ( připravena )"
    else:
        bat_soc = 0
        bat_power_w = 0
        bat_voltage_v = 0.0
        bat_temp_c = 0.0
        bat_status = "Nepřipojena (0 % — připraveno)"

    return {
        "state": "online",
        "run_state_code": run_code,
        "run_state_text": run_map.get(run_code, f"Stav {run_code}"),
        "grid_relay_on": bool(relay & 0x04),
        "inv_relay_on": bool(relay & 0x01),
        "pv1_power_w": pv1_w,
        "pv2_power_w": pv2_w,
        "pv_total_power_w": pv_total_w,
        "pv1_voltage_v": round(r(676) * 0.1, 1),
        "pv1_current_a": round(r(677) * 0.1, 1),
        "pv2_voltage_v": round(r(678) * 0.1, 1),
        "pv2_current_a": round(r(679) * 0.1, 1),
        "grid_v_l1": round(r(598) * 0.1, 1),
        "grid_v_l2": round(r(599) * 0.1, 1),
        "grid_v_l3": round(r(600) * 0.1, 1),
        "grid_freq_hz": round(r(609) * 0.01, 2),
        "eastron_l1_w": eastron_l1,
        "eastron_l2_w": eastron_l2,
        "eastron_l3_w": eastron_l3,
        "eastron_total_w": eastron_total,
        "grid_power_total_w": grid_net_w,
        "grid_import_w": max(0, grid_net_w),
        "grid_export_w": max(0, -grid_net_w),
        "grid_flow_state": grid_flow_state,
        "load_l1_w": l1_corr,
        "load_l2_w": l2_corr,
        "load_l3_w": l3_corr,
        "load_total_w": house_total_corr,
        "raw_load_total_w": load_total,
        "inv_l1_w": s16(r(633)),
        "inv_l2_w": s16(r(634)),
        "inv_l3_w": s16(r(635)),
        "inv_total_w": s16(r(636)),
        "day_pv_kwh": round(r(529) * 0.1, 1),
        "total_pv_kwh": round(u32(r(534), r(535)) * 0.1, 1),
        "day_load_kwh": round(r(526) * 0.1, 1),
        "total_load_kwh": round(u32(r(527), r(528)) * 0.1, 1),
        "day_grid_buy_kwh": round(r(520) * 0.1, 1),
        "total_grid_buy_kwh": round(u32(r(522), r(523)) * 0.1, 1),
        "day_grid_sell_kwh": round(r(521) * 0.1, 1),
        "total_grid_sell_kwh": round(u32(r(524), r(525)) * 0.1, 1),
        "dc_temp_c": dc_temp,
        "heatsink_temp_c": hs_temp,
        "battery_connected": battery_connected,
        "battery_soc_pct": bat_soc,
        "battery_power_w": bat_power_w,
        "battery_voltage_v": bat_voltage_v,
        "battery_temp_c": bat_temp_c,
        "battery_status": bat_status,
        "battery_mode": "Bez baterie (No Battery)" if not battery_connected else "Aktivní baterie",
        "work_mode": "Zero Export To CT (Bez přetoků)",
        "meter_type": "Eastron SDM630MCT V2",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main() -> None:
    try:
        data = poll_deye()
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        if CACHE_FILE.exists():
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            cached["state"] = "cached"
            print(json.dumps(cached, ensure_ascii=False))
        else:
            print(json.dumps({"state": "offline", "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
