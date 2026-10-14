"""Read-only UI replica between the authoritative worker and Streamlit.

When production intelligence runs on Render, Streamlit must not run a second
engine just to populate its tabs.  This module mirrors a strict whitelist of
small/medium SQLite tables that the dashboard reads, plus selected safe JSON
files. Raw prices, intraday bars, secrets, notification outboxes, and model
binaries are deliberately excluded from the public UI replica.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import date, datetime

import pandas as pd

# table -> (row limit, deterministic ordering)
TABLE_SPECS = {
    "dynamic_opportunities": (50, "rank ASC"),
    "dynamic_opportunity_history": (1000, "id DESC"),
    "dynamic_opportunity_pool": (200, "rank ASC"),
    "hold_intelligence": (100, "hold_score DESC"),
    "hold_intelligence_history": (1000, "id DESC"),
    "alert_history": (1000, "id DESC"),
    "outlook": (10, "id ASC"),
    "premarket_evaluations": (1000, "market_date DESC, ticker ASC"),
    "recommendation_runs": (500, "generated_at DESC"),
    "recommendation_history": (5000, "id DESC"),
    "shadow_positions": (2000, "id DESC"),
    "opening_confirmation": (500, "market_date DESC, ticker ASC"),
    "engine_log": (500, "id DESC"),
}

SAFE_JSON_FILES = {
    "model_validation.json",
}

MAX_FILE_BYTES = 5 * 1024 * 1024


def _safe_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        if hasattr(value, "item"):
            value = value.item()
    except Exception:
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _table_exists(con, table):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone())


def _table_columns(con, table):
    return [str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()]


def ensure_remote_schema(con):
    with con.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS asi_ui_table_snapshots (
            table_name TEXT PRIMARY KEY,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload JSONB NOT NULL,
            bridge_version TEXT NOT NULL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS asi_ui_files (
            file_name TEXT PRIMARY KEY,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload JSONB NOT NULL,
            bridge_version TEXT NOT NULL
        )""")


def read_local(db_path, db_dir):
    tables = {}
    if os.path.exists(db_path):
        con = sqlite3.connect(db_path, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            for table, (limit, order_by) in TABLE_SPECS.items():
                if not _table_exists(con, table):
                    continue
                columns = _table_columns(con, table)
                rows = con.execute(
                    f'SELECT * FROM "{table}" ORDER BY {order_by} LIMIT ?',
                    (int(limit),),
                ).fetchall()
                tables[table] = {
                    "columns": columns,
                    "rows": [
                        {str(k): _safe_value(v) for k, v in dict(row).items()}
                        for row in rows
                    ],
                }
        finally:
            con.close()

    files = {}
    for name in SAFE_JSON_FILES:
        path = os.path.join(db_dir, name)
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, "r", encoding="utf-8") as fh:
                files[name] = json.load(fh)
        except Exception:
            continue
    return {"tables": tables, "files": files}


def sync_to_remote(con, db_path, db_dir, bridge_version):
    replica = read_local(db_path, db_dir)
    ensure_remote_schema(con)
    with con.cursor() as cur:
        for table, payload in replica["tables"].items():
            cur.execute(
                """INSERT INTO asi_ui_table_snapshots
                   (table_name,generated_at,payload,bridge_version)
                   VALUES(%s,now(),%s::jsonb,%s)
                   ON CONFLICT(table_name) DO UPDATE SET
                   generated_at=excluded.generated_at,
                   payload=excluded.payload,
                   bridge_version=excluded.bridge_version""",
                (table, json.dumps(payload, ensure_ascii=False), bridge_version),
            )
        for name, payload in replica["files"].items():
            cur.execute(
                """INSERT INTO asi_ui_files
                   (file_name,generated_at,payload,bridge_version)
                   VALUES(%s,now(),%s::jsonb,%s)
                   ON CONFLICT(file_name) DO UPDATE SET
                   generated_at=excluded.generated_at,
                   payload=excluded.payload,
                   bridge_version=excluded.bridge_version""",
                (name, json.dumps(payload, ensure_ascii=False), bridge_version),
            )
    return {
        "tables": len(replica["tables"]),
        "files": len(replica["files"]),
    }


def read_remote(con):
    ensure_remote_schema(con)
    tables = {}
    files = {}
    with con.cursor() as cur:
        cur.execute("SELECT table_name,payload FROM asi_ui_table_snapshots")
        for name, payload in cur.fetchall():
            if str(name) not in TABLE_SPECS:
                continue
            if isinstance(payload, str):
                payload = json.loads(payload)
            tables[str(name)] = payload or {"columns": [], "rows": []}

        cur.execute("SELECT file_name,payload FROM asi_ui_files")
        for name, payload in cur.fetchall():
            if str(name) not in SAFE_JSON_FILES:
                continue
            if isinstance(payload, str):
                payload = json.loads(payload)
            files[str(name)] = payload
    return {"tables": tables, "files": files}


def write_local(replica, db_path, db_dir):
    replica = replica or {}
    tables = replica.get("tables") or {}
    files = replica.get("files") or {}
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    con = sqlite3.connect(db_path, timeout=30)
    written_tables = 0
    try:
        for table, payload in tables.items():
            if table not in TABLE_SPECS:
                continue
            payload = payload or {}
            columns = [str(c) for c in (payload.get("columns") or [])]
            rows = payload.get("rows") or []
            if not columns and rows:
                columns = list(rows[0].keys())
            if not columns:
                continue
            frame = pd.DataFrame(rows, columns=columns)
            frame.to_sql(table, con, if_exists="replace", index=False)
            written_tables += 1
        con.commit()
    finally:
        con.close()

    written_files = 0
    os.makedirs(db_dir, exist_ok=True)
    for name, payload in files.items():
        if name not in SAFE_JSON_FILES:
            continue
        target = os.path.join(db_dir, name)
        temp = target + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, target)
            written_files += 1
        finally:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError:
                pass

    return {"tables": written_tables, "files": written_files}
