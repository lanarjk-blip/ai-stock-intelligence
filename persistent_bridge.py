"""Persistent control-plane bridge for AI Stock Intelligence.

Local SQLite remains a fast cache for the existing analysis code. Critical
user-facing state is persisted in Neon/PostgreSQL by the production worker and
can be restored by Streamlit either directly from DATABASE_URL or, preferably,
through the read-only Render runtime API.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE, "db")
LOCAL_DB = os.path.join(DB_DIR, "market.db")
DATABASE_URL = str(os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL") or "").strip()
BRIDGE_VERSION = "PERSIST-BRIDGE-R2"
SYNC_SECONDS = max(15, int(os.environ.get("ASI_PERSIST_SYNC_SECONDS", "30")))


def postgres_enabled() -> bool:
    return DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def runtime_api_url() -> str:
    env = str(os.environ.get("ASI_RUNTIME_API_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return str(cfg.get("runtime_api_url") or "").strip().rstrip("/")
    except Exception:
        return ""


def api_enabled() -> bool:
    return runtime_api_url().startswith("https://") or runtime_api_url().startswith("http://")


def enabled() -> bool:
    return postgres_enabled() or api_enabled()


def _pg_connect():
    if not postgres_enabled():
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
    if not postgres_enabled():
        return False
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_state (
                key TEXT PRIMARY KEY, value TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_opportunity_snapshot (
                singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                generated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL, bridge_version TEXT NOT NULL)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_premarket_runs (
                market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
                status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_premarket_picks (
                market_date TEXT NOT NULL, rank INTEGER NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(market_date, rank))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_runtime_heartbeat (
                singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                worker_pid INTEGER, worker_generation TEXT, runtime_build TEXT,
                heartbeat_at TIMESTAMPTZ NOT NULL, bridge_version TEXT NOT NULL)""")
        con.commit()
        return True
    finally:
        con.close()


def _local_table_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone())


def _read_local_payload():
    if not os.path.exists(LOCAL_DB):
        return {"state": [], "opportunities": [], "runs": [], "picks": []}
    con = sqlite3.connect(LOCAL_DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        state = [dict(r) for r in con.execute("SELECT key,value FROM state").fetchall()] if _local_table_exists(con, "state") else []
        opportunities = [dict(r) for r in con.execute("SELECT * FROM opportunities ORDER BY rank").fetchall()] if _local_table_exists(con, "opportunities") else []
        runs = [dict(r) for r in con.execute(
            "SELECT market_date,generated_at,status,pick_count,note FROM premarket_runs ORDER BY market_date DESC LIMIT 20"
        ).fetchall()] if _local_table_exists(con, "premarket_runs") else []
        picks = [dict(r) for r in con.execute(
            "SELECT * FROM premarket_picks WHERE market_date IN (SELECT market_date FROM premarket_runs ORDER BY market_date DESC LIMIT 20) ORDER BY market_date,rank"
        ).fetchall()] if _local_table_exists(con, "premarket_picks") and _local_table_exists(con, "premarket_runs") else []
        return {"state": state, "opportunities": opportunities, "runs": runs, "picks": picks}
    finally:
        con.close()


def sync_local_to_remote(runtime_identity=None):
    if not postgres_enabled():
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
                cur.execute(
                    """INSERT INTO asi_opportunity_snapshot(singleton,generated_at,payload,bridge_version)
                       VALUES(1,now(),%s::jsonb,%s)
                       ON CONFLICT(singleton) DO UPDATE SET generated_at=excluded.generated_at,
                       payload=excluded.payload,bridge_version=excluded.bridge_version""",
                    (json.dumps(_json_records(data["opportunities"]), ensure_ascii=False), BRIDGE_VERSION),
                )
            for row in data["runs"]:
                cur.execute(
                    """INSERT INTO asi_premarket_runs(market_date,generated_at,status,pick_count,note,updated_at)
                       VALUES(%s,%s,%s,%s,%s,now()) ON CONFLICT(market_date) DO UPDATE SET
                       generated_at=excluded.generated_at,status=excluded.status,pick_count=excluded.pick_count,
                       note=excluded.note,updated_at=excluded.updated_at""",
                    (str(row.get("market_date") or ""), str(row.get("generated_at") or ""),
                     str(row.get("status") or ""), int(row.get("pick_count") or 0), str(row.get("note") or "")),
                )
            for row in data["picks"]:
                cur.execute(
                    """INSERT INTO asi_premarket_picks(market_date,rank,payload,updated_at)
                       VALUES(%s,%s,%s::jsonb,now()) ON CONFLICT(market_date,rank) DO UPDATE SET
                       payload=excluded.payload,updated_at=excluded.updated_at""",
                    (str(row.get("market_date") or ""), int(row.get("rank") or 0),
                     json.dumps({str(k): _json_value(v) for k, v in row.items()}, ensure_ascii=False)),
                )
            if runtime_identity:
                cur.execute(
                    """INSERT INTO asi_runtime_heartbeat
                       (singleton,worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version)
                       VALUES(1,%s,%s,%s,now(),%s) ON CONFLICT(singleton) DO UPDATE SET
                       worker_pid=excluded.worker_pid,worker_generation=excluded.worker_generation,
                       runtime_build=excluded.runtime_build,heartbeat_at=excluded.heartbeat_at,
                       bridge_version=excluded.bridge_version""",
                    (int(runtime_identity.get("pid") or 0), str(runtime_identity.get("generation") or ""),
                     str(runtime_identity.get("build") or ""), BRIDGE_VERSION),
                )
        con.commit()
        return {"status": "SYNCED", "state": len(data["state"]), "opportunities": len(data["opportunities"]),
                "runs": len(data["runs"]), "picks": len(data["picks"])}
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


def _write_local_payload(state, opportunities, runs, picks):
    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(LOCAL_DB, timeout=30)
    try:
        _ensure_local_core(con)
        if isinstance(state, dict):
            state = [{"key": k, "value": v} for k, v in state.items()]
        for row in state or []:
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                        (str(row.get("key") or ""), str(row.get("value") or "")))
        if opportunities:
            import pandas as pd
            frame = pd.DataFrame(list(opportunities))
            if not frame.empty:
                frame.to_sql("opportunities", con, if_exists="replace", index=False)
        for row in runs or []:
            con.execute(
                "INSERT OR REPLACE INTO premarket_runs(market_date,generated_at,status,pick_count,note) VALUES(?,?,?,?,?)",
                (str(row.get("market_date") or ""), str(row.get("generated_at") or ""),
                 str(row.get("status") or ""), int(row.get("pick_count") or 0), str(row.get("note") or "")),
            )
        for row in picks or []:
            cols = ["market_date","generated_at","rank","ticker","company","decision","confidence","ai_score","risk",
                    "entry_low","entry_high","stop_loss","target_1","target_2","ihsg_regime","reason","source_phase"]
            con.execute(
                """INSERT OR REPLACE INTO premarket_picks
                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [row.get(col) for col in cols],
            )
        con.commit()
        return {"state": len(state or []), "opportunities": len(opportunities or []),
                "runs": len(runs or []), "picks": len(picks or [])}
    finally:
        con.close()


def restore_local_from_postgres():
    ensure_remote_schema()
    con_pg = _pg_connect()
    try:
        with con_pg.cursor() as cur:
            cur.execute("SELECT key,value FROM asi_state")
            state = [{"key": r[0], "value": r[1]} for r in cur.fetchall()]
            cur.execute("SELECT payload FROM asi_opportunity_snapshot WHERE singleton=1")
            snap = cur.fetchone()
            cur.execute("SELECT market_date,generated_at,status,pick_count,note FROM asi_premarket_runs ORDER BY market_date")
            runs = [{"market_date": r[0], "generated_at": r[1], "status": r[2], "pick_count": r[3], "note": r[4]} for r in cur.fetchall()]
            cur.execute("SELECT payload FROM asi_premarket_picks ORDER BY market_date,rank")
            picks = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in cur.fetchall()]
        con_pg.rollback()
    finally:
        con_pg.close()
    opportunities = []
    if snap and snap[0]:
        opportunities = snap[0] if isinstance(snap[0], list) else json.loads(snap[0])
    counts = _write_local_payload(state, opportunities, runs, picks)
    return {"status": "RESTORED_POSTGRES", **counts}


def restore_local_from_api():
    import requests
    url = runtime_api_url()
    if not url:
        return {"status": "DISABLED"}
    response = requests.get(f"{url}/bootstrap", timeout=8, headers={"User-Agent": "AI-Stock-Intelligence/1.8"})
    response.raise_for_status()
    payload = response.json() or {}
    snapshot = payload.get("opportunity_snapshot") or {}
    counts = _write_local_payload(
        payload.get("state") or {},
        snapshot.get("payload") or [],
        payload.get("premarket_runs") or [],
        payload.get("premarket_picks") or [],
    )
    heartbeat = payload.get("heartbeat") or {}
    if heartbeat:
        os.makedirs(DB_DIR, exist_ok=True)
        con = sqlite3.connect(LOCAL_DB, timeout=10)
        try:
            _ensure_local_core(con)
            for key, value in {
                "remote_worker_runtime_build": heartbeat.get("runtime_build", ""),
                "remote_worker_generation": heartbeat.get("worker_generation", ""),
                "remote_worker_heartbeat_at": heartbeat.get("heartbeat_at", ""),
            }.items():
                con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value or "")))
            con.commit()
        finally:
            con.close()
    return {"status": "RESTORED_API", **counts}


def restore_local_from_remote():
    if postgres_enabled():
        return restore_local_from_postgres()
    if api_enabled():
        return restore_local_from_api()
    return {"status": "DISABLED"}


def run_forever(runtime_identity=None):
    if not postgres_enabled():
        return
    try:
        print(f"[PERSIST_BRIDGE] startup {restore_local_from_postgres()}", flush=True)
    except Exception as exc:
        print(f"[PERSIST_BRIDGE] restore error {exc!r}", flush=True)
    while True:
        try:
            print(f"[PERSIST_BRIDGE] sync {sync_local_to_remote(runtime_identity=runtime_identity)}", flush=True)
        except Exception as exc:
            print(f"[PERSIST_BRIDGE] sync error {exc!r}", flush=True)
        time.sleep(SYNC_SECONDS)


def start_worker_bridge(runtime_identity=None):
    if not postgres_enabled():
        return False
    names = {t.name for t in threading.enumerate()}
    if "asi-persist-bridge" in names:
        return True
    threading.Thread(target=run_forever, args=(runtime_identity,), name="asi-persist-bridge", daemon=True).start()
    return True
