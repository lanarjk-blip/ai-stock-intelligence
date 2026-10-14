"""Persistent publication layer for AI Stock Intelligence.

Local SQLite remains the fast analytical cache. Selected runtime outputs are
mirrored to Neon/Postgres so redeploys and UI restarts do not erase the state
that the APK needs to display.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
BUILD_SHA = os.environ.get("RENDER_GIT_COMMIT", os.environ.get("GIT_COMMIT", "")).strip()


def enabled() -> bool:
    return bool(DATABASE_URL)


def pgcon():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL, autocommit=True)


def ensure_schema() -> None:
    if not enabled():
        return
    with pgcon() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runtime_snapshots (
                    snapshot_key text PRIMARY KEY,
                    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    build_sha text NOT NULL DEFAULT '',
                    source text NOT NULL DEFAULT 'backend'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runtime_heartbeat (
                    service_name text PRIMARY KEY,
                    status text NOT NULL,
                    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    build_sha text NOT NULL DEFAULT ''
                )
            """)


def _rows(db_path: str, sql: str, params=()) -> list[dict[str, Any]]:
    try:
        con = sqlite3.connect(db_path, timeout=5)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        con.close()
        return rows
    except Exception:
        return []


def _one(db_path: str, sql: str, params=()) -> dict[str, Any] | None:
    rows = _rows(db_path, sql, params)
    return rows[0] if rows else None


def local_snapshot(db_path: str) -> dict[str, Any]:
    run = _one(db_path, "SELECT market_date,generated_at,status,pick_count,note FROM premarket_runs ORDER BY market_date DESC, generated_at DESC LIMIT 1")
    market_date = str(run.get("market_date")) if run else None
    picks = _rows(db_path, "SELECT market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase FROM premarket_picks WHERE market_date=? ORDER BY rank LIMIT 5", (market_date,)) if market_date else []
    dynamic = _rows(db_path, "SELECT * FROM dynamic_opportunities ORDER BY rank LIMIT 5")
    state_rows = _rows(db_path, "SELECT key,value FROM state")
    state = {str(r.get('key')): r.get('value') for r in state_rows}
    logs = _rows(db_path, "SELECT ts,phase,status,detail FROM engine_log ORDER BY id DESC LIMIT 80")
    return {
        "premarket": {"run": run, "picks": picks},
        "dynamic": dynamic,
        "state": state,
        "logs": logs,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "build_sha": BUILD_SHA,
    }


def publish(db_path: str) -> dict[str, Any]:
    payload = local_snapshot(db_path)
    if not enabled():
        return payload
    ensure_schema()
    with pgcon() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO runtime_snapshots(snapshot_key,payload,updated_at,build_sha,source)
                VALUES('production',%s,now(),%s,'render-worker')
                ON CONFLICT(snapshot_key) DO UPDATE SET
                    payload=EXCLUDED.payload, updated_at=now(), build_sha=EXCLUDED.build_sha, source=EXCLUDED.source
            """, (Jsonb(payload), BUILD_SHA))
            cur.execute("""
                INSERT INTO runtime_heartbeat(service_name,status,detail,updated_at,build_sha)
                VALUES('engine','RUNNING',%s,now(),%s)
                ON CONFLICT(service_name) DO UPDATE SET
                    status='RUNNING',detail=EXCLUDED.detail,updated_at=now(),build_sha=EXCLUDED.build_sha
            """, (Jsonb({"premarket_status": (payload.get('premarket') or {}).get('run',{}).get('status') if (payload.get('premarket') or {}).get('run') else None,
                            "dynamic_count": len(payload.get('dynamic') or []),
                            "snapshot_source": (payload.get('state') or {}).get('snapshot_source')}), BUILD_SHA))
    return payload


def read_remote() -> dict[str, Any] | None:
    if not enabled():
        return None
    ensure_schema()
    with pgcon() as con:
        with con.cursor() as cur:
            cur.execute("SELECT payload FROM runtime_snapshots WHERE snapshot_key='production'")
            row = cur.fetchone()
            return row[0] if row else None
