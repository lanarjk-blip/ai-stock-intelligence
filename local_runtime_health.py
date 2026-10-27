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
import threading
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "db", "market.db")
FILES = (
    "app.py", "worker_base.py", "accuracy_guardrails.py", "dynamic_engine.py",
    "dynamic_opportunity_ui.py", "top5_timeline.py", "recommendation_history.py",
    "market_session.py", "premarket_scheduler.py", "premarket_preliminary.py",
    "premarket_intelligence.py", "premarket_evaluation.py", "premarket_explain.py",
    "closing_overnight.py", "hold_engine.py", "ai_stock_ask.py", "fcm_adapter.py",
    "runtime_supervisor.py", "runtime_health.py", "runtime_health_ui_patch.py", "local_runtime_health.py",
    "persistent_bridge.py", "ui_replica.py", "replica_runtime.py",
    "external_runtime_guard.py", "runtime_api.py", "sitecustomize.py", "config.json",
    "data/idx_holidays.json",
    "focused_timemap.py", "live_evidence.py", "timemap_data.py", "timemap_statistics.py", "trading_monitor.py", "trading_groups.py",
    "timemap_forecast.py", "timemap_forecast_ui.py",
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


def write_worker_heartbeat(pid=0, generation="", build="", fingerprint=None):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    fingerprint = fingerprint or source_fingerprint()
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
            "SELECT key,value FROM state "
            "WHERE key LIKE 'local_worker_%' OR key LIKE 'engine_%'"
        ).fetchall()
        con.close()
        return {str(k): str(v or "") for k, v in rows}
    except Exception:
        return {}


def _age_seconds(value, current):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (current.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def engine_progress_health(state, now=None, cfg=None, status_prefix="LOCAL_"):
    """Validate that the main cycle is making progress, not merely heartbeating."""
    cfg = cfg or _cfg()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    progress_limit = max(300, int(cfg.get("engine_progress_max_age_seconds", 1200)))
    result_limit = max(30, int(cfg.get("engine_result_max_age_minutes", 90))) * 60
    status = str(state.get("engine_cycle_status") or "").upper().strip()
    last_run = str(state.get("engine_last_run") or "").strip()
    progress_at = str(state.get("engine_progress_at") or "").strip()
    stage = str(state.get("engine_progress_stage") or "").strip()
    last_age = _age_seconds(last_run, current)
    progress_age = _age_seconds(progress_at, current)
    base = {
        "engine_cycle_status": status or "UNKNOWN",
        "engine_last_run": last_run,
        "engine_last_run_age_seconds": last_age,
        "engine_progress_at": progress_at,
        "engine_progress_age_seconds": progress_age,
        "engine_progress_stage": stage,
        "engine_progress_max_age_seconds": progress_limit,
        "engine_result_max_age_seconds": result_limit,
    }
    if status == "ERROR":
        return {**base, "ready": False, "status": status_prefix + "ENGINE_ERROR", "reason": "main engine cycle ended in ERROR"}
    if status == "RUNNING":
        if progress_age is None:
            return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "engine cycle started but no progress marker is available yet"}
        if progress_age > progress_limit:
            return {**base, "ready": False, "status": status_prefix + "ENGINE_STALLED", "reason": f"engine progress age {progress_age:.0f}s > {progress_limit}s"}
        if last_age is None:
            return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "first full engine cycle has not completed yet"}
        if last_age > result_limit:
            return {**base, "ready": False, "status": status_prefix + "ENGINE_REFRESHING_STALE", "reason": f"last completed engine result age {last_age:.0f}s > {result_limit}s"}
        return {**base, "ready": True, "status": ("LOCAL_RUNTIME_READY_REFRESHING" if status_prefix == "LOCAL_" else "RUNTIME_READY_REFRESHING"), "reason": ""}
    if last_age is None:
        return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "no completed engine cycle is available"}
    if last_age > result_limit:
        return {**base, "ready": False, "status": status_prefix + "ENGINE_STALE", "reason": f"last completed engine result age {last_age:.0f}s > {result_limit}s"}
    if status not in {"IDLE", ""}:
        return {**base, "ready": False, "status": status_prefix + "ENGINE_INVALID", "reason": f"unknown engine cycle status: {status}"}
    return {**base, "ready": True, "status": ("LOCAL_RUNTIME_READY" if status_prefix == "LOCAL_" else "RUNTIME_READY"), "reason": ""}


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
    engine = engine_progress_health(state, now=current, cfg=cfg, status_prefix="LOCAL_")
    common = {"age_seconds": age, "heartbeat_at": heartbeat, "expected_fingerprint": expected, "worker_fingerprint": worker_fp, "build": state.get("local_worker_runtime_build", ""), "generation": state.get("local_worker_generation", ""), "pid": state.get("local_worker_pid", "")}
    if not engine.get("ready"):
        return {**common, **engine}
    return {**common, **engine}


def start_worker_monitor():
    """Explicit worker bootstrap, including when Python skips sitecustomize."""
    import sys
    import threading
    import time
    if os.environ.get('AI_ENGINE_WORKER') != '1' or os.path.basename(str(sys.argv[0] or '')) != 'worker_base.py':
        return False
    import runtime_supervisor
    identity = dict(runtime_supervisor.CLAIM or {})
    startup_fingerprint = identity.get('fingerprint') or source_fingerprint()
    def heartbeat():
        return write_worker_heartbeat(pid=os.getpid(), generation=identity.get('generation',''),
                                      build=identity.get('build',''), fingerprint=startup_fingerprint)
    # Synchronous first heartbeat proves this real worker has reached bootstrap.
    heartbeat()
    def pulse():
        while True:
            try:
                heartbeat()
            except Exception as exc:
                print(f'[LOCAL_RUNTIME_HEALTH] heartbeat failed: {type(exc).__name__}',flush=True)
            time.sleep(30)
    def watch():
        while True:
            time.sleep(20)
            if source_fingerprint() != startup_fingerprint:
                os.execv(sys.executable,[sys.executable,os.path.join(BASE,'worker_base.py')])
    names={t.name for t in threading.enumerate()}
    for name,target in [('asi-local-heartbeat',pulse),('asi-runtime-watchdog',watch)]:
        if name not in names:
            threading.Thread(target=target,name=name,daemon=True).start()
    return True


_RECOVERY_LAST = None
_RECOVERY_LOCK = threading.Lock()


def request_worker_recovery():
    """Bounded recovery; the existing supervisor verifies/replaces stale owners."""
    import time
    import sys
    import subprocess
    import runtime_health
    global _RECOVERY_LAST
    if runtime_health.external_runtime_expected() or os.environ.get('AI_ENGINE_WORKER') == '1':
        return False
    if not _RECOVERY_LOCK.acquire(blocking=False):
        return False
    try:
        now=time.monotonic()
        if _RECOVERY_LAST is not None and now-_RECOVERY_LAST<180:
            return False
        if health().get('status') not in {'LOCAL_RUNTIME_MISSING','LOCAL_BUILD_MISMATCH'}:
            return False
        env=dict(os.environ)
        env['AI_ENGINE_WORKER']='1'
        env['PYTHONPATH']=os.pathsep.join(filter(None,[BASE,env.get('PYTHONPATH','')]))
        subprocess.Popen([sys.executable,os.path.join(BASE,'worker_base.py')],cwd=BASE,env=env,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        _RECOVERY_LAST=now
        return True
    finally:
        _RECOVERY_LOCK.release()
