"""Runtime consistency watchdog for AI Stock Intelligence V1.8.52.

The background worker is a detached long-running process. Streamlit can reload
application source without replacing that child process, leaving an old worker
running new config. When this module is loaded in worker mode it watches the
runtime-critical files and replaces the worker process when their fingerprint
changes.
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
    "dynamic_opportunity_ui.py",
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
