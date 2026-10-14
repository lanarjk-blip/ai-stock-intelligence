"""Runtime consistency, H-10 Pre-Market deadline, persistence, and provider recovery."""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_IDENTITY = None
_IS_REAL_WORKER = (
    os.environ.get("AI_ENGINE_WORKER") == "1"
    and os.path.basename(str(sys.argv[0] or "")) == "worker_base.py"
)

if _IS_REAL_WORKER:
    try:
        import runtime_supervisor
        _RUNTIME_IDENTITY = dict(runtime_supervisor.CLAIM or {})
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[RUNTIME_SUPERVISOR] startup error: {exc!r}", flush=True)

# Restore the persistent control plane before app.py initializes.  If the UI is
# configured to read from the Render runtime API, suppress app.py's legacy
# local worker spawn so Render remains the only production engine.
try:
    import persistent_bridge
    if persistent_bridge.enabled():
        restored = persistent_bridge.restore_local_from_remote()
        print(f"[PERSIST_BRIDGE] pre-app restore {restored}", flush=True)
        if _IS_REAL_WORKER:
            persistent_bridge.start_worker_bridge(_RUNTIME_IDENTITY)
        elif os.environ.get("AI_ENGINE_WORKER") != "1" and persistent_bridge.api_enabled():
            import external_runtime_guard
            external_runtime_guard.install()
except Exception as exc:
    print(f"[PERSIST_BRIDGE] startup degraded: {exc!r}", flush=True)

_WATCH = (
    "worker_base.py",
    "accuracy_guardrails.py",
    "config.json",
    "app.py",
    "dynamic_engine.py",
    "dynamic_opportunity_ui.py",
    "premarket_scheduler.py",
    "premarket_preliminary.py",
    "premarket_ui_patch.py",
    "persistent_bridge.py",
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
elif os.environ.get("AI_ENGINE_WORKER") != "1":
    try:
        import premarket_ui_patch
        premarket_ui_patch.install()
    except Exception:
        pass


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
        fallback_url = text_url.replace("query1.finance.yahoo.com", "query2.finance.yahoo.com", 1)
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
