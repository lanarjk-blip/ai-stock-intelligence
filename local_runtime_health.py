"""Local Streamlit worker freshness and build-consistency guard.

External Render mode already has a remote heartbeat. Local mode needs the same
safety because a detached worker can outlive a Streamlit rerun/deploy and keep
writing stale recommendations. Actionable UI is allowed only when the local
worker heartbeat is fresh and its source fingerprint matches the UI process.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "db", "market.db")
FILES = (
    "app.py", "worker_base.py", "accuracy_guardrails.py", "dynamic_engine.py",
    "dynamic_opportunity_ui.py", "market_session.py", "premarket_scheduler.py",
    "premarket_preliminary.py", "runtime_supervisor.py", "sitecustomize.py",
)


def source_fingerprint() -> str:
    h = hashlib.sha256()
    for name in FILES:
        path = os.path.join(BASE, name)
        h.update(name.encode("utf-8"))
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:20]


def _cfg():
    try:
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def write_worker_heartbeat(pid=0, generation="", build=""):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    fingerprint = source_fingerprint()
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
        values = {
            "local_worker_heartbeat_at": now,
            "local_worker_source_fingerprint": fingerprint,
            "local_worker_pid": str(int(pid or 0)),
            "local_worker_generation": str(generation or ""),
            "local_worker_runtime_build": str(build or ""),
        }
        for key, value in values.items():
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, value))
        con.commit()
    finally:
        con.close()
    return values


def _state():
    try:
        con = sqlite3.connect(DB, timeout=3)
        rows = con.execute(
            "SELECT key,value FROM state WHERE key LIKE 'local_worker_%'"
        ).fetchall()
        con.close()
        return {str(k): str(v or "") for k, v in rows}
    except Exception:
        return {}


def health(now=None, max_age_seconds=None):
    cfg = _cfg()
    limit = int(max_age_seconds or cfg.get("runtime_heartbeat_max_age_seconds", 180))
    limit = max(30, limit)
    expected = source_fingerprint()
    state = _state()
    heartbeat = state.get("local_worker_heartbeat_at", "")
    worker_fp = state.get("local_worker_source_fingerprint", "")
    if not heartbeat:
        return {"ready": False, "status": "LOCAL_RUNTIME_MISSING", "reason": "local worker heartbeat missing", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}
    try:
        dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = max(0.0, (current.astimezone(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return {"ready": False, "status": "LOCAL_RUNTIME_INVALID", "reason": "local worker heartbeat invalid", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}
    if worker_fp != expected:
        return {"ready": False, "status": "LOCAL_BUILD_MISMATCH", "age_seconds": age, "reason": "UI and worker source fingerprints differ", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}
    if age > limit:
        return {"ready": False, "status": "LOCAL_RUNTIME_STALE", "age_seconds": age, "reason": f"local worker heartbeat age {age:.0f}s > {limit}s", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}
    return {"ready": True, "status": "LOCAL_RUNTIME_READY", "age_seconds": age, "heartbeat_at": heartbeat, "expected_fingerprint": expected, "worker_fingerprint": worker_fp, "build": state.get("local_worker_runtime_build", ""), "generation": state.get("local_worker_generation", ""), "pid": state.get("local_worker_pid", "")}
