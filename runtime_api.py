"""Read-only production runtime API.

This service exposes only the market-engine control plane required by the
Streamlit/APK client. It never exposes DATABASE_URL, credentials, raw logs, or
write actions. Render runs it separately from the background worker; both use
the same Neon DATABASE_URL.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

import persistent_bridge

app = FastAPI(title="AI Stock Intelligence Runtime API", version="1.0")

_SENSITIVE_STATE_PARTS = (
    "token", "secret", "password", "credential", "service_account",
    "private_key", "api_key", "whatsapp", "fcm_device",
)


def _safe_state_key(key) -> bool:
    text = str(key or "").lower()
    return bool(text) and not any(part in text for part in _SENSITIVE_STATE_PARTS)


def _require_db():
    if not persistent_bridge.postgres_enabled():
        raise HTTPException(status_code=503, detail="persistent database unavailable")
    persistent_bridge.ensure_remote_schema()


def _jsonb(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


@app.get("/health")
def health():
    _require_db()
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute(
                """SELECT worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version
                   FROM asi_runtime_heartbeat WHERE singleton=1"""
            )
            row = cur.fetchone()
        con.rollback()
    finally:
        con.close()

    heartbeat = None
    if row:
        heartbeat = {
            "worker_pid": row[0],
            "worker_generation": row[1],
            "runtime_build": row[2],
            "heartbeat_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
            "bridge_version": row[4],
        }
    return {
        "ok": True,
        "service": "ai-stock-intelligence-runtime",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "heartbeat": heartbeat,
    }


@app.get("/bootstrap")
def bootstrap():
    """Return the latest safe control-plane snapshot for a fresh UI runtime."""
    _require_db()
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT key,value FROM asi_state")
            state_rows = cur.fetchall()
            cur.execute(
                "SELECT generated_at,payload,bridge_version FROM asi_opportunity_snapshot WHERE singleton=1"
            )
            snap = cur.fetchone()
            cur.execute(
                """SELECT market_date,generated_at,status,pick_count,note
                   FROM asi_premarket_runs ORDER BY market_date DESC LIMIT 20"""
            )
            runs = cur.fetchall()
            cur.execute(
                """SELECT market_date,rank,payload FROM asi_premarket_picks
                   WHERE market_date IN (
                     SELECT market_date FROM asi_premarket_runs ORDER BY market_date DESC LIMIT 20
                   ) ORDER BY market_date,rank"""
            )
            picks = cur.fetchall()
            cur.execute(
                """SELECT worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version
                   FROM asi_runtime_heartbeat WHERE singleton=1"""
            )
            heartbeat = cur.fetchone()
        con.rollback()
    finally:
        con.close()

    state = {
        str(k): str(v or "")
        for k, v in state_rows
        if _safe_state_key(k)
    }
    snapshot = {
        "generated_at": None,
        "payload": [],
        "bridge_version": None,
    }
    if snap:
        snapshot = {
            "generated_at": snap[0].isoformat() if hasattr(snap[0], "isoformat") else str(snap[0]),
            "payload": _jsonb(snap[1]) or [],
            "bridge_version": snap[2],
        }

    run_payload = [
        {
            "market_date": str(r[0]),
            "generated_at": str(r[1]),
            "status": str(r[2]),
            "pick_count": int(r[3] or 0),
            "note": str(r[4] or ""),
        }
        for r in runs
    ]
    pick_payload = []
    for market_date, rank, payload in picks:
        item = _jsonb(payload) or {}
        item["market_date"] = str(item.get("market_date") or market_date)
        item["rank"] = int(item.get("rank") or rank or 0)
        pick_payload.append(item)

    hb = None
    if heartbeat:
        hb = {
            "worker_pid": heartbeat[0],
            "worker_generation": heartbeat[1],
            "runtime_build": heartbeat[2],
            "heartbeat_at": heartbeat[3].isoformat() if hasattr(heartbeat[3], "isoformat") else str(heartbeat[3]),
            "bridge_version": heartbeat[4],
        }

    return {
        "state": state,
        "opportunity_snapshot": snapshot,
        "premarket_runs": run_payload,
        "premarket_picks": pick_payload,
        "heartbeat": hb,
    }
