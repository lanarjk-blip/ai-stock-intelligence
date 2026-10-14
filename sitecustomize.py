"""Runtime consistency and narrow provider recovery for V1.8.52.

The detached worker self-reloads when runtime-critical source changes. IHSG
also gets the same Yahoo query1 -> query2 failover already used by equity
collection so missing market context cannot silently collapse JST training.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_WATCH = (
    "worker_base.py",
    "accuracy_guardrails.py",
    "config.json",
    "app.py",
    "dynamic_engine.py",
    "dynamic_opportunity_ui.py",
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


if os.environ.get("AI_ENGINE_WORKER") == "1":
    threading.Thread(target=_watch_worker, name="asi-runtime-watchdog", daemon=True).start()


# Targeted IHSG failover. Do not alter ordinary stock/API requests.
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
        """Retry IHSG on Yahoo query2 even when query1 raises outright.

        The previous wrapper only retried when query1 returned an unusable
        response. A timeout/DNS/TLS error escaped before failover, which left
        fetch_ihsg() empty and in turn could produce zero JST training rows.
        """
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
    # Network recovery must never prevent interpreter startup.
    pass
