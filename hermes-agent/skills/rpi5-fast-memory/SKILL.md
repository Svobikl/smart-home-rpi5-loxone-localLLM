---
name: rpi5-fast-memory
description: Ultra-fast SQLite FTS5 + WAL + 256MB mmap + RAM temp_store semantic/episodic memory engine tuned specifically for Raspberry Pi 5 (<2ms latency).
version: 1.0.0
metadata:
  hermes:
    tags: [memory, rpi5, sqlite, fts5, wal, performance]
---

# RPi5 Fast Memory Skill (`rpi5-fast-memory`)

## Why Use This Skill on Raspberry Pi 5
- Tuned specifically for Raspberry Pi 5 Cortex-A76 + Linux page cache:
  - `PRAGMA journal_mode=WAL`
  - `PRAGMA synchronous=NORMAL`
  - `PRAGMA temp_store=MEMORY`
  - `PRAGMA mmap_size=268435456` (256 MB memory-mapped I/O)
  - `PRAGMA cache_size=-64000` (64 MB RAM cache)
  - SQLite **FTS5 with BM25 ranking** (`porter unicode61` tokenizer) for `< 1.5 ms` recall latency and zero SD/NVMe wear.

## Commands

### 1. Store a Memory Fact / Decision / Lesson (`store`)
```bash
python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py store --category "smart-home" --title "Deye 15kW IP" --content "FVE Deye 15kW běží na <DEYE_INVERTER_HOST>, telemetrie v HA jako sensor.fve_celkovy_vykon" --tags "deye,fve,mysmarthome"
```

### 2. Instant BM25 Full-Text Recall (`search`)
```bash
python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py search "Deye FVE výkon"
```

### 3. Benchmark & Health Status (`benchmark` / `stats`)
```bash
python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py benchmark
python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py stats
```
