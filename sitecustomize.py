"""Runtime consistency, H-10 scheduling, persistence, and provider recovery."""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_IDENTITY = None
_ARGV_TEXT = " ".join(str(x) for x in sys.argv).lower()
_IS_REAL_WORKER = (
    os.environ.get("AI_ENGINE_WORKER") == "1"
    and os.path.basename(str(sys.argv[0] or "")) == "worker_base.py"
)
_IS_RUNTIME_API = (
    "uvicorn" in os.path.basename(str(sys.argv[0] or "")).lower()
    or "runtime_api:app" in _ARGV_TEXT
)

if _IS_REAL_WORKER:
    try:
        import runtime_supervisor
        _RUNTIME_IDENTITY = dict(runtime_supervisor.CLAIM or {})
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[RUNTIME_SUPERVISOR] startup error: {exc!r}", flush=True)

# Production persistence startup order:
# 1) cold Render worker restores full SQLite/model checkpoint;
# 2) newest control-plane rows overlay the checkpoint;
# 3) worker starts private checkpoint/control sync and sanitized UI replication;
# 4) Streamlit pulls the sanitized API replica before app.py renders, then keeps
#    refreshing that local read cache in one background thread.
try:
    import persistent_bridge
    if _IS_REAL_WORKER and persistent_bridge.postgres_enabled():
        checkpoint = persistent_bridge.restore_worker_checkpoint_if_needed()
        print(f"[PERSIST_BRIDGE] checkpoint restore {checkpoint}", flush=True)
        restored = persistent_bridge.restore_local_from_postgres()
        print(f"[PERSIST_BRIDGE] control restore {restored}", flush=True)
        persistent_bridge.start_worker_bridge(_RUNTIME_IDENTITY)
        import replica_runtime
        replica_runtime.start_worker_replica()
    elif (
        not _IS_RUNTIME_API
        and os.environ.get("AI_ENGINE_WORKER") != "1"
        and persistent_bridge.enabled()
    ):
        if persistent_bridge.api_enabled():
            import external_runtime_guard
            external_runtime_guard.install()
            import replica_runtime
            pulled = replica_runtime.pull_ui_once()
            print(f"[UI_REPLICA] initial UI pull {pulled}", flush=True)
            replica_runtime.start_ui_pull()
        else:
            restored = persistent_bridge.restore_local_from_remote()
            print(f"[PERSIST_BRIDGE] UI direct restore {restored}", flush=True)
except Exception as exc:
    print(f"[PERSIST_BRIDGE] startup degraded: {exc!r}", flush=True)

_WATCH = (
    "worker_base.py",
    "accuracy_guardrails.py",
    "config.json",
    "app.py",
    "dynamic_engine.py",
    "dynamic_opportunity_ui.py",
    "market_session.py",
    "data/idx_holidays.json",
    "premarket_scheduler.py",
    "premarket_preliminary.py",
    "premarket_ui_patch.py",
    "runtime_health.py",
    "runtime_health_ui_patch.py",
    "persistent_bridge.py",
    "ui_replica.py",
    "replica_runtime.py",
    "external_runtime_guard.py",
    "runtime_supervisor.py",
    "runtime_api.py",
    "render.yaml",
    "sitecustomize.py",
)


def _fingerprint():
    h = hashlib.sha256()
    for name in _WATCH:
        path = os.path.join(_BASE, name)
        h.update(name.encode("utf-8"))
        try:
            stat = os.stat(path)
            h.update(str(stat.st_mtime_ns).encode("ascii"))
            h.update(str(stat.st_size).encode("ascii"))
        except OSError:
            h.update(b"missing")
    return h.hexdigest()


def _watch_worker():
    initial = _fingerprint()
    while True:
        time.sleep(20)
        if _fingerprint() != initial:
            try:
                print("[RUNTIME] RELOAD source/config changed; replacing worker process", flush=True)
            finally:
                os.execv(sys.executable, [sys.executable, os.path.join(_BASE, "worker_base.py")])


def _run_premarket_scheduler():
    try:
        time.sleep(3)
        import premarket_scheduler
        premarket_scheduler.run_forever()
    except Exception as exc:
        print(f"[PREMARKET_H10] startup error: {exc!r}", flush=True)


def _run_premarket_preliminary():
    try:
        time.sleep(4)
        import premarket_preliminary
        premarket_preliminary.run_forever()
    except Exception as exc:
        print(f"[PREMARKET_PRELIMINARY] startup error: {exc!r}", flush=True)


if _IS_REAL_WORKER:
    threading.Thread(target=_watch_worker, name="asi-runtime-watchdog", daemon=True).start()
    threading.Thread(target=_run_premarket_scheduler, name="asi-premarket-h10", daemon=True).start()
    threading.Thread(target=_run_premarket_preliminary, name="asi-premarket-preliminary", daemon=True).start()
elif not _IS_RUNTIME_API and os.environ.get("AI_ENGINE_WORKER") != "1":
    try:
        import premarket_ui_patch
        premarket_ui_patch.install()
        import runtime_health_ui_patch
        runtime_health_ui_patch.install()
    except Exception:
        pass


# Targeted IHSG Yahoo query1 -> query2 failover.
try:
    import requests

    _ORIGINAL_GET = requests.get
    _IHSG_PATHS = ("/v8/finance/chart/%5EJKSE", "/v8/finance/chart/^JKSE")

    def _usable_chart_response(response):
        try:
            if response is None or int(response.status_code) >= 400:
                return False
            payload = response.json() or {}
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                return False
            first = result[0] or {}
            timestamps = first.get("timestamp") or []
            quotes = ((first.get("indicators") or {}).get("quote") or [])
            return bool(timestamps and quotes)
        except Exception:
            return False

    def _get_with_ihsg_failover(url, *args, **kwargs):
        text_url = str(url)
        is_query1_ihsg = (
            "query1.finance.yahoo.com" in text_url
            and any(path in text_url for path in _IHSG_PATHS)
        )
        if not is_query1_ihsg:
            return _ORIGINAL_GET(url, *args, **kwargs)
        primary_response = None
        primary_error = None
        try:
            primary_response = _ORIGINAL_GET(url, *args, **kwargs)
            if _usable_chart_response(primary_response):
                return primary_response
        except Exception as exc:
            primary_error = exc
        fallback_url = text_url.replace(
            "query1.finance.yahoo.com", "query2.finance.yahoo.com", 1
        )
        try:
            fallback = _ORIGINAL_GET(fallback_url, *args, **kwargs)
            if _usable_chart_response(fallback):
                return fallback
            if primary_response is not None:
                return primary_response
            return fallback
        except Exception:
            if primary_response is not None:
                return primary_response
            if primary_error is not None:
                raise primary_error
            raise

    requests.get = _get_with_ihsg_failover
except Exception:
    pass
