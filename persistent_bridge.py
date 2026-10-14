"""Persistent control-plane bridge for AI Stock Intelligence.

The trading engine still uses SQLite locally for fast analytical reads, but the
critical user-facing state must survive Streamlit/worker restarts.  When
``DATABASE_URL`` is configured (Neon/PostgreSQL), this module mirrors:

- state key/value records
- the latest Opportunity snapshot
- Pre-Market runs and picks

On process startup the remote copy is restored into local SQLite before normal
runtime work.  During worker operation a small daemon mirrors local changes to
PostgreSQL.  With no DATABASE_URL the module is a strict no-op, preserving the
current local development/CI path.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE, "db")
LOCAL_DB = os.path.join(DB_DIR, "market.db")
DATABASE_URL = str(os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL") or "").strip()
BRIDGE_VERSION = "PERSIST-BRIDGE-R1"
SYNC_SECONDS = max(15, int(os.environ.get("ASI_PERSIST_SYNC_SECONDS", "30")))


def enabled() -> bool:
    return DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def _pg_connect():
    if not enabled():
        return None
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=6, autocommit=False)


def _json_value(value):
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


def _json_records(rows):
    return [
        {str(key): _json_value(value) for key, value in dict(row).items()}
        for row in rows
    ]


def ensure_remote_schema():
    if not enabled():
        return False
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_opportunity_snapshot (
                    singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                    generated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    bridge_version TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_premarket_runs (
                    market_date TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pick_count INTEGER NOT NULL,
                    note TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_premarket_picks (
                    market_date TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(market_date, rank)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_runtime_heartbeat (
                    singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                    worker_pid INTEGER,
                    worker_generation TEXT,
                    runtime_build TEXT,
                    heartbeat_at TIMESTAMPTZ NOT NULL,
                    bridge_version TEXT NOT NULL
                )
            """)
        con.commit()
        return True
    finally:
        con.close()


def _local_table_exists(con, name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _read_local_payload():
    if not os.path.exists(LOCAL_DB):
        return {"state": [], "opportunities": [], "runs": [], "picks": []}
    con = sqlite3.connect(LOCAL_DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        state = []
        opportunities = []
        runs = []
        picks = []
        if _local_table_exists(con, "state"):
            state = [dict(r) for r in con.execute("SELECT key,value FROM state").fetchall()]
        if _local_table_exists(con, "opportunities"):
            opportunities = [dict(r) for r in con.execute("SELECT * FROM opportunities ORDER BY rank").fetchall()]
        if _local_table_exists(con, "premarket_runs"):
            runs = [dict(r) for r in con.execute(
                "SELECT market_date,generated_at,status,pick_count,note FROM premarket_runs ORDER BY market_date DESC LIMIT 20"
            ).fetchall()]
        if _local_table_exists(con, "premarket_picks"):
            picks = [dict(r) for r in con.execute(
                "SELECT * FROM premarket_picks WHERE market_date IN (SELECT market_date FROM premarket_runs ORDER BY market_date DESC LIMIT 20) ORDER BY market_date,rank"
            ).fetchall()]
        return {
            "state": state,
            "opportunities": opportunities,
            "runs": runs,
            "picks": picks,
        }
    finally:
        con.close()


def sync_local_to_remote(runtime_identity=None):
    if not enabled():
        return {"status": "DISABLED"}
    ensure_remote_schema()
    data = _read_local_payload()
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            for row in data["state"]:
                cur.execute(
                    """INSERT INTO asi_state(key,value,updated_at) VALUES(%s,%s,now())
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                    (str(row.get("key") or ""), str(row.get("value") or "")),
                )

            if data["opportunities"]:
                payload = json.dumps(_json_records(data["opportunities"]), ensure_ascii=False)
                cur.execute(
                    """INSERT INTO asi_opportunity_snapshot(singleton,generated_at,payload,bridge_version)
                       VALUES(1,now(),%s::jsonb,%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                         generated_at=excluded.generated_at,
                         payload=excluded.payload,
                         bridge_version=excluded.bridge_version""",
                    (payload, BRIDGE_VERSION),
                )

            for row in data["runs"]:
                cur.execute(
                    """INSERT INTO asi_premarket_runs
                       (market_date,generated_at,status,pick_count,note,updated_at)
                       VALUES(%s,%s,%s,%s,%s,now())
                       ON CONFLICT(market_date) DO UPDATE SET
                         generated_at=excluded.generated_at,status=excluded.status,
                         pick_count=excluded.pick_count,note=excluded.note,updated_at=excluded.updated_at""",
                    (
                        str(row.get("market_date") or ""),
                        str(row.get("generated_at") or ""),
                        str(row.get("status") or ""),
                        int(row.get("pick_count") or 0),
                        str(row.get("note") or ""),
                    ),
                )

            for row in data["picks"]:
                payload = json.dumps(
                    {str(k): _json_value(v) for k, v in row.items()},
                    ensure_ascii=False,
                )
                cur.execute(
                    """INSERT INTO asi_premarket_picks(market_date,rank,payload,updated_at)
                       VALUES(%s,%s,%s::jsonb,now())
                       ON CONFLICT(market_date,rank) DO UPDATE SET
                         payload=excluded.payload,updated_at=excluded.updated_at""",
                    (str(row.get("market_date") or ""), int(row.get("rank") or 0), payload),
                )

            if runtime_identity:
                cur.execute(
                    """INSERT INTO asi_runtime_heartbeat
                       (singleton,worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version)
                       VALUES(1,%s,%s,%s,now(),%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                         worker_pid=excluded.worker_pid,
                         worker_generation=excluded.worker_generation,
                         runtime_build=excluded.runtime_build,
                         heartbeat_at=excluded.heartbeat_at,
                         bridge_version=excluded.bridge_version""",
                    (
                        int(runtime_identity.get("pid") or 0),
                        str(runtime_identity.get("generation") or ""),
                        str(runtime_identity.get("build") or ""),
                        BRIDGE_VERSION,
                    ),
                )
        con.commit()
        return {
            "status": "SYNCED",
            "state": len(data["state"]),
            "opportunities": len(data["opportunities"]),
            "runs": len(data["runs"]),
            "picks": len(data["picks"]),
        }
    finally:
        con.close()


def _ensure_local_core(con):
    con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_date TEXT NOT NULL, generated_at TEXT NOT NULL,
        rank INTEGER, ticker TEXT, company TEXT, decision TEXT,
        confidence REAL, ai_score REAL, risk TEXT,
        entry_low REAL, entry_high REAL, stop_loss REAL,
        target_1 REAL, target_2 REAL, ihsg_regime TEXT,
        reason TEXT, source_phase TEXT,
        UNIQUE(market_date, rank))""")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")


def restore_local_from_remote():
    if not enabled():
        return {"status": "DISABLED"}
    ensure_remote_schema()
    con_pg = _pg_connect()
    try:
        with con_pg.cursor() as cur:
            cur.execute("SELECT key,value FROM asi_state")
            state = cur.fetchall()
            cur.execute("SELECT payload FROM asi_opportunity_snapshot WHERE singleton=1")
            snap = cur.fetchone()
            cur.execute("SELECT market_date,generated_at,status,pick_count,note FROM asi_premarket_runs ORDER BY market_date")
            runs = cur.fetchall()
            cur.execute("SELECT payload FROM asi_premarket_picks ORDER BY market_date,rank")
            pick_rows = cur.fetchall()
        con_pg.rollback()
    finally:
        con_pg.close()

    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(LOCAL_DB, timeout=30)
    try:
        _ensure_local_core(con)
        for key, value in state:
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (str(key), str(value or "")))

        if snap and snap[0]:
            import pandas as pd
            raw = snap[0]
            if isinstance(raw, str):
                raw = json.loads(raw)
            frame = pd.DataFrame(list(raw or []))
            if not frame.empty:
                frame.to_sql("opportunities", con, if_exists="replace", index=False)

        for market_date, generated_at, status, pick_count, note in runs:
            con.execute(
                "INSERT OR REPLACE INTO premarket_runs(market_date,generated_at,status,pick_count,note) VALUES(?,?,?,?,?)",
                (str(market_date), str(generated_at), str(status), int(pick_count or 0), str(note or "")),
            )

        for (payload,) in pick_rows:
            row = payload
            if isinstance(row, str):
                row = json.loads(row)
            if not row:
                continue
            columns = [
                "market_date","generated_at","rank","ticker","company","decision","confidence",
                "ai_score","risk","entry_low","entry_high","stop_loss","target_1","target_2",
                "ihsg_regime","reason","source_phase",
            ]
            values = [row.get(col) for col in columns]
            con.execute(
                """INSERT OR REPLACE INTO premarket_picks
                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        con.commit()
        return {
            "status": "RESTORED",
            "state": len(state),
            "opportunities": 0 if not snap else len(snap[0] or []),
            "runs": len(runs),
            "picks": len(pick_rows),
        }
    finally:
        con.close()


def run_forever(runtime_identity=None):
    if not enabled():
        return
    try:
        result = restore_local_from_remote()
        print(f"[PERSIST_BRIDGE] startup {result}", flush=True)
    except Exception as exc:
        print(f"[PERSIST_BRIDGE] restore error {exc!r}", flush=True)

    while True:
        try:
            result = sync_local_to_remote(runtime_identity=runtime_identity)
            print(f"[PERSIST_BRIDGE] sync {result}", flush=True)
        except Exception as exc:
            print(f"[PERSIST_BRIDGE] sync error {exc!r}", flush=True)
        time.sleep(SYNC_SECONDS)


def start_worker_bridge(runtime_identity=None):
    if not enabled():
        return False
    names = {t.name for t in threading.enumerate()}
    if "asi-persist-bridge" in names:
        return True
    threading.Thread(
        target=run_forever,
        args=(runtime_identity,),
        name="asi-persist-bridge",
        daemon=True,
    ).start()
    return True
