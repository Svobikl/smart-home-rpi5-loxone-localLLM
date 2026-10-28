---
name: heartbeat-monitor
description: Periodic and on-demand heartbeat monitoring for Raspberry Pi 5 hardware (CPU temp, RAM, disk), Hermes Agent DB, Home Assistant, Matter Server, FVE Deye 15kW, Loxone, and TCL TV.
version: 1.0.0
metadata:
  hermes:
    tags: [heartbeat, monitoring, healthcheck, rpi5, smart-home]
---

# Heartbeat Monitor Skill (`heartbeat-monitor`)

## Purpose
Monitors the health of Raspberry Pi 5 (`<HA_SERVER_HOST>`), Hermes Agent (`state.db` + `rpi5_fast_memory.db`), and MySmartHome smart home subsystems every 5 minutes via `hermes-heartbeat.timer` (`hermes-heartbeat.service`) and on-demand via CLI.

## Commands
```bash
python3 ~/.hermes/skills/heartbeat-monitor/scripts/heartbeat_check.py
```
