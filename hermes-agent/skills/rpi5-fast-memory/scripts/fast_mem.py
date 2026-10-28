#!/usr/bin/env python3
"""Ultra-fast SQLite FTS5 + WAL + mmap memory engine optimized for Raspberry Pi 5."""
import os
import sys
import time
import json
import sqlite3
import argparse
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memories" / "rpi5_fast_memory.db"
STATE_DB_PATH = Path.home() / ".hermes" / "state.db"

def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA mmap_size=268435456;")
    conn.execute("PRAGMA cache_size=-64000;")
    conn.execute("PRAGMA wal_autocheckpoint=1000;")
    return conn

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS memory_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL DEFAULT 'general',
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        importance INTEGER NOT NULL DEFAULT 5,
        access_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries(category);
    CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory_entries(updated_at DESC);

    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        title,
        content,
        category,
        tags,
        content='memory_entries',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );

    CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_entries BEGIN
        INSERT INTO memory_fts(rowid, title, content, category, tags)
        VALUES (new.id, new.title, new.content, new.category, new.tags);
    END;
    CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_entries BEGIN
        INSERT INTO memory_fts(memory_fts, rowid, title, content, category, tags)
        VALUES ('delete', old.id, old.title, old.content, old.category, old.tags);
    END;
    CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_entries BEGIN
        INSERT INTO memory_fts(memory_fts, rowid, title, content, category, tags)
        VALUES ('delete', old.id, old.title, old.content, old.category, old.tags);
        INSERT INTO memory_fts(rowid, title, content, category, tags)
        VALUES (new.id, new.title, new.content, new.category, new.tags);
    END;
    """)
    conn.commit()

def optimize_hermes_state_db():
    if STATE_DB_PATH.exists():
        try:
            c = get_conn(STATE_DB_PATH)
            c.execute("PRAGMA optimize;")
            c.close()
        except Exception:
            pass

def store_entry(conn, category: str, title: str, content: str, tags: str = "", importance: int = 5) -> dict:
    t0 = time.perf_counter()
    now = time.time()
    existing = conn.execute("SELECT id FROM memory_entries WHERE title = ? AND category = ?", (title, category)).fetchone()
    if existing:
        row_id = existing["id"]
        conn.execute(
            "UPDATE memory_entries SET content = ?, tags = ?, importance = ?, updated_at = ? WHERE id = ?",
            (content, tags, importance, now, row_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO memory_entries(category, title, content, tags, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (category, title, content, tags, importance, now, now),
        )
        row_id = cur.lastrowid
    conn.commit()
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return {"ok": True, "id": row_id, "latency_ms": round(dt_ms, 3)}

def search_entries(conn, query: str, limit: int = 10) -> dict:
    t0 = time.perf_counter()
    tokens = [t.strip() for t in query.replace('"', ' ').split() if t.strip()]
    if not tokens:
        rows = conn.execute("SELECT * FROM memory_entries ORDER BY importance DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
    else:
        fts_query = " OR ".join(f'"{t}"*' for t in tokens)
        rows = conn.execute(
            """
            SELECT m.*, bm25(memory_fts, 5.0, 2.0, 1.5, 1.5) AS rank
            FROM memory_fts
            JOIN memory_entries m ON m.id = memory_fts.rowid
            WHERE memory_fts MATCH ?
            ORDER BY rank ASC, m.importance DESC
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    results = [dict(r) for r in rows]
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return {"ok": True, "query": query, "count": len(results), "latency_ms": round(dt_ms, 3), "results": results}

def main():
    parser = argparse.ArgumentParser(description="RPi5 Fast SQLite FTS5 Memory Engine")
    sub = parser.add_subparsers(dest="cmd")

    p_store = sub.add_parser("store")
    p_store.add_argument("--category", default="general")
    p_store.add_argument("--title", required=True)
    p_store.add_argument("--content", required=True)
    p_store.add_argument("--tags", default="")
    p_store.add_argument("--importance", type=int, default=5)

    p_search = sub.add_parser("search")
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--limit", type=int, default=10)

    sub.add_parser("stats")
    sub.add_parser("benchmark")

    args = parser.parse_args()
    conn = get_conn()
    init_db(conn)
    optimize_hermes_state_db()

    if args.cmd == "store":
        res = store_entry(conn, args.category, args.title, args.content, args.tags, args.importance)
    elif args.cmd == "search":
        res = search_entries(conn, args.query, args.limit)
    elif args.cmd == "benchmark":
        # Warm up WAL page cache on connection open, then measure steady-state write + FTS5 BM25 read
        store_entry(conn, "benchmark", "RPi5 Warmup", "Warmup entry", "warmup", 1)
        w_res = store_entry(conn, "benchmark", "RPi5 FTS5 Speed Test", "MySmartHome FVE Deye 15kW Loxone TCL TV MiniMax 3.1 Flash", "rpi5,benchmark", 5)
        r_res = search_entries(conn, "MySmartHome Deye MiniMax", 5)
        jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        mm = conn.execute("PRAGMA mmap_size;").fetchone()[0]
        res = {
            "ok": True,
            "journal_mode": jm,
            "mmap_size": mm,
            "write_latency_ms": w_res["latency_ms"],
            "read_fts5_latency_ms": r_res["latency_ms"],
            "sub_5ms_verified": (w_res["latency_ms"] < 5.0 and r_res["latency_ms"] < 5.0),
        }
    else:
        cnt = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        mm = conn.execute("PRAGMA mmap_size;").fetchone()[0]
        res = {"ok": True, "db_path": str(DB_PATH), "entries": cnt, "journal_mode": jm, "mmap_size": mm}

    print(json.dumps(res, ensure_ascii=False, indent=2))
    conn.close()

if __name__ == "__main__":
    main()
