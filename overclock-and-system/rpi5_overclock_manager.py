#!/usr/bin/env python3
"""Raspberry Pi 5 Overclocking, SDRAM Bank Interleaving (SDRAM_BANKLOW=1), Fake NUMA & V3D 3D GPU Manager."""

from __future__ import annotations

import re

VALID_SDRAM_BANKLOW = frozenset({0, 1, 2, 3})
VALID_NUMA_NODES = frozenset({1, 2, 4, 8})


def build_rpi5_overclock_config_block(
    arm_freq: int = 2800,
    v3d_freq: int = 1080,
    gpu_freq: int = 1000,
    over_voltage_delta: int = 0,
    allow_overvoltage: bool = False,
) -> str:
    """Build a validated [pi5] / [all] config.txt block for Raspberry Pi 5 CPU, V3D 3D GPU, PCIe Gen 3 & PWM cooling."""
    if not allow_overvoltage and over_voltage_delta > 0:
        raise ValueError(
            f"over_voltage_delta={over_voltage_delta} is disallowed when allow_overvoltage=False (zero-overvoltage mode)"
        )
    if over_voltage_delta < -50000 or over_voltage_delta > 75000:
        raise ValueError(f"over_voltage_delta={over_voltage_delta} is outside safe bounds [-50000, 75000]")
    if arm_freq < 1500 or arm_freq > 3200:
        raise ValueError(f"arm_freq={arm_freq} MHz is outside safe bounds [1500, 3200]")
    if v3d_freq < 500 or v3d_freq > 1200:
        raise ValueError(f"v3d_freq={v3d_freq} MHz is outside safe bounds [500, 1200]")
    if gpu_freq < 500 or gpu_freq > 1150:
        raise ValueError(f"gpu_freq={gpu_freq} MHz is outside safe bounds [500, 1150]")

    lines = [
        "# --- BEGIN RPi5 SAFE OVERCLOCK & 3D/COOLING TUNING ---",
        "[pi5]",
        f"arm_freq={arm_freq}",
        f"gpu_freq={gpu_freq}",
        f"v3d_freq={v3d_freq}",
        f"hevc_freq={gpu_freq}",
        f"isp_freq={gpu_freq}",
        f"over_voltage_delta={over_voltage_delta}",
        "dtparam=pciex1",
        "dtparam=pciex1_gen=3",
        "gpio=45=op,dh",
        "dtparam=cooling_fan=on",
        "dtparam=fan_temp0=35000,fan_temp0_hyst=5000,fan_temp0_speed=255",
        "dtparam=fan_temp1=40000,fan_temp1_hyst=5000,fan_temp1_speed=255",
        "dtparam=fan_temp2=45000,fan_temp2_hyst=5000,fan_temp2_speed=255",
        "dtparam=fan_temp3=50000,fan_temp3_hyst=5000,fan_temp3_speed=255",
        "# --- END RPi5 SAFE OVERCLOCK & 3D/COOLING TUNING ---",
    ]
    return "\n".join(lines) + "\n"


def apply_eeprom_sdram_banklow(eeprom_config_text: str, banklow: int = 1) -> str:
    """Idempotently set SDRAM_BANKLOW=<banklow> in rpi-eeprom-config text."""
    if banklow not in VALID_SDRAM_BANKLOW:
        raise ValueError(f"Invalid banklow={banklow}; expected one of {sorted(VALID_SDRAM_BANKLOW)}")
    lines: list[str] = []
    found = False
    for raw_line in eeprom_config_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("SDRAM_BANKLOW="):
            if not found:
                lines.append(f"SDRAM_BANKLOW={banklow}")
                found = True
        else:
            lines.append(raw_line)
    if not found:
        lines.append(f"SDRAM_BANKLOW={banklow}")
    return "\n".join(lines) + "\n"


def apply_cmdline_fake_numa(cmdline_text: str, nodes: int = 8) -> str:
    """Ensure single-line /boot/firmware/cmdline.txt includes root=, numa=fake=<nodes>, and numa_policy=interleave."""
    if nodes not in VALID_NUMA_NODES:
        raise ValueError(f"Invalid NUMA nodes={nodes}; expected one of {sorted(VALID_NUMA_NODES)}")
    cleaned = " ".join(cmdline_text.split()).strip()
    if not cleaned or "root=" not in cleaned or "No such file" in cleaned:
        raise ValueError("cmdline_text must be a valid kernel command line containing 'root='")
    tokens = [
        tok
        for tok in cleaned.split(" ")
        if tok and not tok.startswith("numa=fake=") and not tok.startswith("numa_policy=")
    ]
    tokens.append(f"numa=fake={nodes}")
    tokens.append("numa_policy=interleave")
    return " ".join(tokens) + "\n"


def evaluate_stability_and_throttling(
    throttled_output: str,
    max_temp_c: float,
    errors: int,
    max_allowed_temp_c: float = 82.0,
) -> dict[str, object]:
    """Evaluate vcgencmd get_throttled, max temperature, and workload error count for 100% stability."""
    match = re.search(r"0x([0-9a-fA-F]+)", throttled_output or "")
    throttled_int = int(match.group(1), 16) if match else -1
    if errors > 0:
        return {
            "stable": False,
            "reason": "computation_or_memory_errors",
            "throttled_int": throttled_int,
            "max_temp_c": max_temp_c,
            "errors": errors,
        }
    if throttled_int != 0:
        return {
            "stable": False,
            "reason": "throttling_or_undervoltage",
            "throttled_int": throttled_int,
            "max_temp_c": max_temp_c,
            "errors": errors,
        }
    if max_temp_c >= max_allowed_temp_c:
        return {
            "stable": False,
            "reason": "temperature_exceeded",
            "throttled_int": throttled_int,
            "max_temp_c": max_temp_c,
            "errors": errors,
        }
    return {
        "stable": True,
        "reason": "ok",
        "throttled_int": throttled_int,
        "max_temp_c": max_temp_c,
        "errors": errors,
    }
