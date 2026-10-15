"""Authoritative runtime freshness guard.

When Streamlit is configured to consume the external Render runtime, a cached
READY quality state is not enough.  Actionable UI must also prove that the
remote worker heartbeat is recent.  Otherwise a network outage or dead worker
could leave yesterday's snapshot looking deceptively healthy.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "db", "market.db")
DEFAULT_MAX_AGE_SECONDS = 180


def _config():
    try:
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def external_runtime_url() -> str:
    env = str(os.environ.get("ASI_RUNTIME_API_URL") or "").strip().rstrip("/")
    if env:
        return env
    cfg = _config()
    return str(cfg.get("runtime_api_url") or "").strip().rstrip("/")


def external_runtime_expected() -> bool:
    url = external_runtime_url()
    return url.startswith("https://") or url.startswith("http://")


def _remote_state():
    try:
        con = sqlite3.connect(DB, timeout=3)
        rows = con.execute(
            "SELECT key,value FROM state WHERE key IN "
            "('remote_worker_runtime_build','remote_worker_generation',"
            "'remote_worker_pid','remote_worker_heartbeat_at','remote_worker_bridge_version',"
            "'engine_last_run','engine_cycle_started_at','engine_cycle_completed_at',"
            "'engine_cycle_status','engine_progress_at','engine_progress_stage','engine_cycle_error_at')"
        ).fetchall()
        con.close()
        return {str(k): str(v or "") for k, v in rows}
    except Exception:
        return {}


def remote_runtime_health(now=None, max_age_seconds=None):
    required = external_runtime_expected()
    if not required:
        try:
            import local_runtime_health
            local = dict(local_runtime_health.health(now=now, max_age_seconds=max_age_seconds) or {})
            local["required"] = True
            return local
        except Exception as exc:
            return {
                "required": True,
                "ready": False,
                "status": "LOCAL_RUNTIME_INVALID",
                "age_seconds": None,
                "heartbeat_at": "",
                "build": "",
                "generation": "",
                "pid": 0,
                "reason": f"local runtime guard failed: {exc}",
            }

    cfg = _config()
    limit = int(
        max_age_seconds
        if max_age_seconds is not None
        else cfg.get("runtime_heartbeat_max_age_seconds", DEFAULT_MAX_AGE_SECONDS)
    )
    limit = max(30, limit)
    state = _remote_state()
    heartbeat = str(state.get("remote_worker_heartbeat_at") or "").strip()
    if not heartbeat:
        return {
            "required": True,
            "ready": False,
            "status": "RUNTIME_MISSING",
            "age_seconds": None,
            "heartbeat_at": "",
            "build": state.get("remote_worker_runtime_build", ""),
            "generation": state.get("remote_worker_generation", ""),
            "pid": int(state.get("remote_worker_pid") or 0) if str(state.get("remote_worker_pid") or "").isdigit() else 0,
            "reason": "remote worker heartbeat is missing",
        }

    try:
        dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = max(0.0, (current.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return {
            "required": True,
            "ready": False,
            "status": "RUNTIME_INVALID",
            "age_seconds": None,
            "heartbeat_at": heartbeat,
            "build": state.get("remote_worker_runtime_build", ""),
            "generation": state.get("remote_worker_generation", ""),
            "pid": 0,
            "reason": "remote worker heartbeat timestamp is invalid",
        }

    heartbeat_ready = age <= float(limit)
    try:
        pid = int(state.get("remote_worker_pid") or 0)
    except Exception:
        pid = 0
    common = {
        "required": True,
        "age_seconds": age,
        "max_age_seconds": limit,
        "heartbeat_at": heartbeat,
        "build": state.get("remote_worker_runtime_build", ""),
        "generation": state.get("remote_worker_generation", ""),
        "pid": pid,
    }
    if not heartbeat_ready:
        return {**common, "ready": False, "status": "RUNTIME_STALE", "reason": f"remote worker heartbeat age {age:.0f}s > {limit}s"}
    try:
        import local_runtime_health
        engine = local_runtime_health.engine_progress_health(state, now=now, cfg=cfg, status_prefix="RUNTIME_")
    except Exception as exc:
        return {**common, "ready": False, "status": "RUNTIME_ENGINE_INVALID", "reason": f"engine progress guard failed: {exc}"}
    return {**common, **engine}
