"""Read-only production runtime API.

This service exposes only the market-engine data required by the Streamlit/APK
client. It never exposes DATABASE_URL, raw price history, model binaries,
notification outboxes, credentials, or write actions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

import local_runtime_health
import persistent_bridge
import ui_replica

app = FastAPI(title="AI Stock Intelligence Runtime API", version="1.3")

_SENSITIVE_STATE_PARTS = (
    "token", "secret", "password", "credential", "service_account",
    "private_key", "api_key", "whatsapp", "fcm_device",
)
_ENGINE_HEALTH_KEYS = (
    "engine_cycle_status",
    "engine_last_run",
    "engine_progress_at",
    "engine_progress_stage",
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


def _timestamp_age_seconds(value, now):
    if value is None:
        return None
    try:
        if hasattr(value, "astimezone"):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def runtime_health_snapshot(heartbeat_row, engine_state, now=None, cfg=None):
    """Return operational health without exposing private state.

    HTTP service liveness and worker/engine readiness are intentionally separate.
    Render can keep this API alive while `ok` reports that the market engine is
    missing, stale, warming, or stalled.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cfg = cfg or local_runtime_health._cfg()
    heartbeat_limit = max(30, int(cfg.get("runtime_heartbeat_max_age_seconds", 180)))

    heartbeat = None
    if heartbeat_row:
        heartbeat = {
            "worker_pid": heartbeat_row[0],
            "worker_generation": heartbeat_row[1],
            "runtime_build": heartbeat_row[2],
            "heartbeat_at": (
                heartbeat_row[3].isoformat()
                if hasattr(heartbeat_row[3], "isoformat")
                else str(heartbeat_row[3])
            ),
            "bridge_version": heartbeat_row[4],
        }

    if not heartbeat_row:
        return {
            "ok": False,
            "status": "RUNTIME_MISSING",
            "heartbeat": None,
            "heartbeat_age_seconds": None,
            "heartbeat_max_age_seconds": heartbeat_limit,
            "engine": local_runtime_health.engine_progress_health(
                engine_state or {}, now=current, cfg=cfg, status_prefix=""
            ),
        }

    heartbeat_age = _timestamp_age_seconds(heartbeat_row[3], current)
    if heartbeat_age is None:
        return {
            "ok": False,
            "status": "RUNTIME_INVALID",
            "heartbeat": heartbeat,
            "heartbeat_age_seconds": None,
            "heartbeat_max_age_seconds": heartbeat_limit,
            "engine": local_runtime_health.engine_progress_health(
                engine_state or {}, now=current, cfg=cfg, status_prefix=""
            ),
        }
    if heartbeat_age > heartbeat_limit:
        return {
            "ok": False,
            "status": "RUNTIME_STALE",
            "heartbeat": heartbeat,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_max_age_seconds": heartbeat_limit,
            "engine": local_runtime_health.engine_progress_health(
                engine_state or {}, now=current, cfg=cfg, status_prefix=""
            ),
        }

    engine = local_runtime_health.engine_progress_health(
        engine_state or {}, now=current, cfg=cfg, status_prefix=""
    )
    return {
        "ok": bool(engine.get("ready")),
        "status": str(engine.get("status") or "RUNTIME_INVALID"),
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_max_age_seconds": heartbeat_limit,
        "engine": engine,
    }


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
            cur.execute(
                "SELECT key,value FROM asi_state WHERE key = ANY(%s)",
                (list(_ENGINE_HEALTH_KEYS),),
            )
            engine_state = {str(k): str(v or "") for k, v in cur.fetchall()}
        con.rollback()
    finally:
        con.close()

    snapshot = runtime_health_snapshot(row, engine_state)
    return {
        "service": "ai-stock-intelligence-runtime",
        "service_ok": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        **snapshot,
    }


@app.get("/bootstrap")
def bootstrap():
    """Return the latest safe production snapshot for the Streamlit UI cache."""
    _require_db()
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT key,value FROM asi_state")
            state_rows = cur.fetchall()
            cur.execute(
                "SELECT generated_at,payload,bridge_version "
                "FROM asi_opportunity_snapshot WHERE singleton=1"
            )
            snap = cur.fetchone()
            cur.execute(
                """SELECT market_date,generated_at,status,pick_count,note
                   FROM asi_premarket_runs ORDER BY market_date DESC LIMIT 20"""
            )
            runs = cur.fetchall()
            cur.execute(
                """SELECT p.market_date,p.rank,p.payload FROM asi_premarket_picks p
                   JOIN (
                     SELECT market_date,pick_count FROM asi_premarket_runs
                     ORDER BY market_date DESC LIMIT 20
                   ) r ON r.market_date=p.market_date
                   WHERE p.rank BETWEEN 1 AND r.pick_count
                   ORDER BY p.market_date,p.rank"""
            )
            picks = cur.fetchall()
            cur.execute(
                """SELECT worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version
                   FROM asi_runtime_heartbeat WHERE singleton=1"""
            )
            heartbeat = cur.fetchone()
        replica = ui_replica.read_remote(con)
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
        "ui_replica": replica,
        "heartbeat": hb,
    }
