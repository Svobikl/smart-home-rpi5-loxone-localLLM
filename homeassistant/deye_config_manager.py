"""Solarman V5 Modbus RTU-over-TCP Configuration & Diagnostic Manager for Deye SUN-15K-SG05LP3-EU-SM2."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import time
from pathlib import Path
from typing import Dict, List, Sequence


ALLOWED_CONFIG_REGISTERS: Dict[int, str] = {
    104: "Zero Export Power (W)",
    111: "Battery Operation Mode (0=Use Batt V, 1=Lithium/Cap, 2=No Battery)",
    130: "Battery Grid Charging (0=Disabled, 1=Enabled)",
    142: "Limit Control Function / System Work Mode (0=Selling First, 1=Zero Export To Load, 2=Zero Export To CT)",
    145: "Solar Sell (0=Disabled, 1=Enabled)",
    146: "Time of Use Selling (Bit0: 0=Disabled, 1=Enabled)",
    228: "Comm Board Function (Bit2-3: 10=Beep Disable, 11=Beep Enable)",
    344: "Grid Check from Meter or CT (Bit0: 0=CT, 1=Meter)",
    345: "External Meter Manufacturer (1=Eastron, 2=CHNT)",
}


def modbus_crc16(data: bytes) -> int:
    """Compute standard Modbus RTU CRC-16 (polynomial 0xA001, initial value 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_modbus_read_frame(slave_id: int, start_reg: int, count: int) -> bytes:
    """Build a Modbus RTU Function Code 0x03 (Read Holding Registers) frame."""
    if not (1 <= count <= 125):
        raise ValueError(f"Invalid register read count: {count}")
    if not (0 <= start_reg <= 0xFFFF):
        raise ValueError(f"Invalid start register: {start_reg}")
    payload = struct.pack(">BBHH", slave_id, 0x03, start_reg, count)
    crc = modbus_crc16(payload)
    return payload + struct.pack("<H", crc)


def build_modbus_write_multiple_frame(
    slave_id: int, start_reg: int, values: Sequence[int]
) -> bytes:
    """Build a Modbus RTU Function Code 0x10 (Write Multiple Holding Registers) frame."""
    if not values or len(values) > 100:
        raise ValueError(f"Invalid write register count: {len(values)}")
    if not (0 <= start_reg <= 0xFFFF):
        raise ValueError(f"Invalid start register: {start_reg}")
    for val in values:
        if not (0 <= val <= 0xFFFF):
            raise ValueError(f"Out-of-range 16-bit register value: {val}")
    reg_count = len(values)
    byte_count = reg_count * 2
    header = struct.pack(">BBHHB", slave_id, 0x10, start_reg, reg_count, byte_count)
    body = b"".join(struct.pack(">H", v) for v in values)
    payload = header + body
    crc = modbus_crc16(payload)
    return payload + struct.pack("<H", crc)


def build_solarman_v5_frame(
    logger_sn: int, modbus_frame: bytes, seq: int = 1
) -> bytes:
    """Wrap a raw Modbus RTU frame in a Solarman V5 TCP request envelope."""
    frame_type = b"\x02"
    sensor_type = b"\x00\x00"
    delivery_time = b"\x00\x00\x00\x00"
    power_on_time = b"\x00\x00\x00\x00"
    offset_time = b"\x00\x00\x00\x00"
    business_field = (
        frame_type
        + sensor_type
        + delivery_time
        + power_on_time
        + offset_time
        + modbus_frame
    )
    length = len(business_field)
    control_code = struct.pack("<H", 0x4510)
    seq_bytes = struct.pack("<H", seq & 0xFFFF)
    sn_bytes = struct.pack("<I", logger_sn)
    header = (
        b"\xa5"
        + struct.pack("<H", length)
        + control_code
        + seq_bytes
        + sn_bytes
    )
    body = header[1:] + business_field
    checksum = sum(body) & 0xFF
    return header + business_field + struct.pack("B", checksum) + b"\x15"


def parse_solarman_v5_read_response(
    raw: bytes, expected_count: int, slave_id: int = 1
) -> List[int]:
    """Extract and CRC-verify Modbus 0x03 register values from a Solarman V5 response."""
    expected_byte_count = expected_count * 2
    expected_modbus_len = 3 + expected_byte_count + 2
    if len(raw) < expected_modbus_len + 10:
        raise ValueError(
            f"Truncated Solarman V5 read response: {len(raw)} bytes"
        )
    for idx in range(20, len(raw) - expected_modbus_len + 1):
        if (
            raw[idx] == slave_id
            and raw[idx + 1] == 0x03
            and raw[idx + 2] == expected_byte_count
        ):
            candidate = raw[idx : idx + expected_modbus_len]
            crc_calc = modbus_crc16(candidate[:-2])
            crc_recv = struct.unpack("<H", candidate[-2:])[0]
            if crc_calc == crc_recv:
                data_bytes = candidate[3:-2]
                return [
                    struct.unpack(">H", data_bytes[i * 2 : (i + 1) * 2])[0]
                    for i in range(expected_count)
                ]
    raise ValueError(
        f"Invalid Modbus read response (no CRC-verified 0x03 frame for count={expected_count})"
    )


def parse_solarman_v5_write_response(
    raw: bytes, start_reg: int, count: int, slave_id: int = 1
) -> bool:
    """Verify a Modbus 0x10 (Write Multiple Registers) response inside a Solarman V5 frame."""
    expected_modbus_len = 8
    if len(raw) < expected_modbus_len + 10:
        raise ValueError(
            f"Truncated Solarman V5 write response: {len(raw)} bytes"
        )
    expected_prefix = struct.pack(">BBHH", slave_id, 0x10, start_reg, count)
    for idx in range(20, len(raw) - expected_modbus_len + 1):
        if raw[idx : idx + 6] == expected_prefix:
            candidate = raw[idx : idx + 8]
            crc_calc = modbus_crc16(candidate[:-2])
            crc_recv = struct.unpack("<H", candidate[-2:])[0]
            if crc_calc == crc_recv:
                return True
    raise ValueError(
        f"Invalid Modbus 0x10 write response for start_reg={start_reg}, count={count}"
    )


def validate_target_config_writes(writes: Dict[int, int]) -> None:
    """Ensure all target registers are in the explicit configuration whitelist and U16 range."""
    for reg, val in writes.items():
        if reg not in ALLOWED_CONFIG_REGISTERS:
            raise ValueError(
                f"Register {reg} is not in allowed configuration register whitelist"
            )
        if not (0 <= val <= 0xFFFF):
            raise ValueError(
                f"Out-of-range 16-bit register value for reg {reg}: {val}"
            )


def load_deye_ip_from_env(env_path: str = "~/.hermes/.env") -> str:
    """Load only DEYE_IP from the environment file without exposing credentials."""
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Environment file not found: {env_path}")
    with open(env_path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            if key.strip() == "DEYE_IP":
                return val.strip().strip('"').strip("'")
    raise KeyError("DEYE_IP not found in environment file")


class DeyeSolarmanClient:
    """TCP client for Solarman V5 Modbus RTU communication with Deye hybrid inverters."""

    def __init__(self, ip: str, logger_sn: int, port: int = 8899, slave_id: int = 1) -> None:
        self.ip = ip
        self.logger_sn = logger_sn
        self.port = port
        self.slave_id = slave_id
        self._seq = 1

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        return seq

    def read_registers(self, start_reg: int, count: int, retries: int = 3) -> List[int]:
        """Read contiguous holding registers via Function Code 0x03 with CRC verification."""
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                modbus_frame = build_modbus_read_frame(self.slave_id, start_reg, count)
                v5_frame = build_solarman_v5_frame(
                    self.logger_sn, modbus_frame, seq=self._next_seq()
                )
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(4.0)
                    sock.connect((self.ip, self.port))
                    sock.sendall(v5_frame)
                    raw = sock.recv(2048)
                return parse_solarman_v5_read_response(
                    raw, expected_count=count, slave_id=self.slave_id
                )
            except Exception as exc:
                last_err = exc
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(
            f"Failed to read registers {start_reg}..{start_reg + count - 1}: {last_err}"
        )

    def write_register_verified(self, reg: int, value: int, retries: int = 3) -> int:
        """Write a single holding register via Function Code 0x10 and verify via 0x03 readback."""
        validate_target_config_writes({reg: value})
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                modbus_frame = build_modbus_write_multiple_frame(
                    self.slave_id, reg, [value]
                )
                v5_frame = build_solarman_v5_frame(
                    self.logger_sn, modbus_frame, seq=self._next_seq()
                )
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(4.0)
                    sock.connect((self.ip, self.port))
                    sock.sendall(v5_frame)
                    raw = sock.recv(2048)
                parse_solarman_v5_write_response(
                    raw, start_reg=reg, count=1, slave_id=self.slave_id
                )
                time.sleep(0.35)
                readback = self.read_registers(reg, 1)[0]
                return readback
            except Exception as exc:
                last_err = exc
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Failed to write register {reg}={value}: {last_err}")


def to_s16(val: int) -> int:
    """Convert unsigned 16-bit integer to signed 16-bit integer."""
    return val - 0x10000 if val >= 0x8000 else val


def backup_registers(client: DeyeSolarmanClient, output_path: Path) -> Dict[int, int]:
    """Read registers 0..499 in blocks of 20 and save full backup JSON."""
    reg_map: Dict[int, int] = {}
    for start in range(0, 500, 20):
        vals = client.read_registers(start, 20)
        for offset, v in enumerate(vals):
            reg_map[start + offset] = v
        time.sleep(0.15)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "inverter_ip": client.ip,
        "logger_sn": client.logger_sn,
        "registers": {str(k): v for k, v in sorted(reg_map.items())},
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return reg_map


def collect_telemetry_snapshot(client: DeyeSolarmanClient) -> Dict[str, object]:
    """Read configuration and live telemetry blocks for diagnostics."""
    cfg_100 = client.read_registers(100, 50)
    time.sleep(0.15)
    cfg_220 = client.read_registers(220, 20)
    time.sleep(0.15)
    cfg_340 = client.read_registers(340, 10)
    time.sleep(0.15)
    tel_500 = client.read_registers(500, 60)
    time.sleep(0.15)
    tel_580 = client.read_registers(580, 90)

    def r_cfg(addr: int) -> int:
        if 100 <= addr < 150:
            return cfg_100[addr - 100]
        if 220 <= addr < 240:
            return cfg_220[addr - 220]
        if 340 <= addr < 350:
            return cfg_340[addr - 340]
        raise KeyError(addr)

    def r_tel(addr: int) -> int:
        if 500 <= addr < 560:
            return tel_500[addr - 500]
        if 580 <= addr < 670:
            return tel_580[addr - 580]
        raise KeyError(addr)

    return {
        "config": {
            "reg_104_zero_export_power_w": r_cfg(104),
            "reg_111_battery_mode": r_cfg(111),
            "reg_130_grid_charge": r_cfg(130),
            "reg_142_limit_control_work_mode": r_cfg(142),
            "reg_143_max_solar_sell_power_w": r_cfg(143),
            "reg_145_solar_sell": r_cfg(145),
            "reg_146_tou_selling": r_cfg(146),
            "reg_228_comm_board_func_hex": f"0x{r_cfg(228):04X}",
            "reg_228_comm_board_func_int": r_cfg(228),
            "reg_344_grid_check_meter_or_ct": r_cfg(344),
            "reg_345_meter_manufacturer": r_cfg(345),
        },
        "telemetry": {
            "reg_500_run_state": r_tel(500),
            "reg_551_switch_state": r_tel(551),
            "reg_552_relay_state_hex": f"0x{r_tel(552):04X}",
            "reg_553_warnings_1_hex": f"0x{r_tel(553):04X}",
            "reg_554_warnings_2_hex": f"0x{r_tel(554):04X}",
            "reg_555_fault_1_hex": f"0x{r_tel(555):04X}",
            "reg_556_fault_2_hex": f"0x{r_tel(556):04X}",
            "reg_557_fault_3_hex": f"0x{r_tel(557):04X}",
            "reg_558_fault_4_hex": f"0x{r_tel(558):04X}",
            "reg_587_battery_voltage_v": round(r_tel(587) * 0.01, 2),
            "reg_588_battery_soc_pct": r_tel(588),
            "reg_598_grid_v_l1": round(r_tel(598) * 0.1, 1),
            "reg_599_grid_v_l2": round(r_tel(599) * 0.1, 1),
            "reg_600_grid_v_l3": round(r_tel(600) * 0.1, 1),
            "reg_604_grid_side_p_l1_w": to_s16(r_tel(604)),
            "reg_605_grid_side_p_l2_w": to_s16(r_tel(605)),
            "reg_606_grid_side_p_l3_w": to_s16(r_tel(606)),
            "reg_607_grid_side_p_total_w": to_s16(r_tel(607)),
            "reg_609_grid_freq_hz": round(r_tel(609) * 0.01, 2),
            "reg_616_ext_ct_p_l1_w": to_s16(r_tel(616)),
            "reg_617_ext_ct_p_l2_w": to_s16(r_tel(617)),
            "reg_618_ext_ct_p_l3_w": to_s16(r_tel(618)),
            "reg_619_ext_ct_p_total_w": to_s16(r_tel(619)),
            "reg_622_inv_p_l1_w": to_s16(r_tel(622)),
            "reg_623_inv_p_l2_w": to_s16(r_tel(623)),
            "reg_624_inv_p_l3_w": to_s16(r_tel(624)),
            "reg_625_inv_p_total_w": to_s16(r_tel(625)),
            "reg_650_load_p_l1_w": to_s16(r_tel(650)),
            "reg_651_load_p_l2_w": to_s16(r_tel(651)),
            "reg_652_load_p_l3_w": to_s16(r_tel(652)),
            "reg_653_load_p_total_w": to_s16(r_tel(653)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deye SUN-15K-SG05LP3-EU-SM2 Solarman V5 Modbus Configurator"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Backup registers, apply target configuration writes, and verify telemetry.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Read and display current configuration and telemetry without writing.",
    )
    args = parser.parse_args()

    ip = load_deye_ip_from_env()
    logger_sn = int(os.environ.get("DEYE_LOGGER_SN", "1234567890"))
    client = DeyeSolarmanClient(ip=ip, logger_sn=logger_sn)

    if args.status_only:
        snap = collect_telemetry_snapshot(client)
        print(json.dumps(snap, indent=2))
        return

    if args.apply:
        backup_path = Path(".agent-workspace/outputs/deye_registers_backup.json")
        print(f"[1/4] Backing up holding registers 0..499 to {backup_path} ...")
        before_regs = backup_registers(client, backup_path)
        before_snap = collect_telemetry_snapshot(client)

        # Target configuration updates:
        # Reg 111 = 2 (No Battery mode)
        # Reg 130 = 0 (Battery Grid Charge Disabled)
        # Reg 142 = 2 (Zero Export To CT)
        # Reg 145 = 0 (Solar Sell Disabled)
        # Reg 104 = 20 (Zero Export Power = 20W safety buffer)
        # Reg 228 = clear bit 2 to set bits 2-3 from 11 (Beep Enable) to 10 (Beep Disable)
        current_228 = before_regs.get(228, 0x0EEF)
        target_228 = (current_228 & ~0x000C) | 0x0008  # Bits 2-3 = 10 (0x08)

        target_writes: Dict[int, int] = {
            111: 2,
            130: 0,
            142: 2,
            145: 0,
            104: 20,
            228: target_228,
            344: 1,
            345: 1,
        }

        write_results: Dict[str, Dict[str, object]] = {}
        print("[2/4] Applying Modbus 0x10 configuration writes ...")
        for reg, target_val in target_writes.items():
            old_val = before_regs.get(reg)
            readback_val = client.write_register_verified(reg, target_val)
            write_results[str(reg)] = {
                "description": ALLOWED_CONFIG_REGISTERS[reg],
                "before": old_val,
                "target": target_val,
                "after_readback": readback_val,
                "verified_match": readback_val == target_val,
            }
            print(
                f"  Reg {reg:3d} ({ALLOWED_CONFIG_REGISTERS[reg]}): "
                f"{old_val} -> {target_val} (readback={readback_val})"
            )

        print("[3/4] Waiting 12 seconds for inverter DSP / relay transition ...")
        time.sleep(12.0)

        print("[4/4] Reading post-configuration telemetry snapshot ...")
        after_snap = collect_telemetry_snapshot(client)

        report_path = Path(".agent-workspace/outputs/deye_post_config_report.json")
        report_payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "inverter_ip": ip,
            "logger_sn": logger_sn,
            "write_results": write_results,
            "before_snapshot": before_snap,
            "after_snapshot": after_snap,
        }
        report_path.write_text(
            json.dumps(report_payload, indent=2), encoding="utf-8"
        )
        print(f"Post-configuration report written to {report_path}")
        print(json.dumps(report_payload, indent=2))


if __name__ == "__main__":
    main()
