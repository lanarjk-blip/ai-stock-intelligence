"""Render backend for the production stock engine.

The analytical engine keeps its local SQLite cache for speed, while published
runtime output is mirrored to Neon through persistent_runtime.py. FastAPI gives
the APK/Streamlit UI a stable read surface and a health endpoint.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

os.environ.setdefault("AI_ENGINE_WORKER", "1")
os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

from fastapi import FastAPI, HTTPException

import app as stock_app
import worker_base
import premarket_scheduler
import premarket_preliminary
import persistent_runtime

api = FastAPI(title="AI Stock Intelligence Runtime", version="1.0")
_started = False
_lock = threading.Lock()


def _safe_thread(name, target):
    live = {t.name for t in threading.enumerate()}
    if name in live:
        return
    threading.Thread(target=target, name=name, daemon=True).start()


def _publisher_loop():
    while True:
        try:
            persistent_runtime.publish(stock_app.DB)
        except Exception as exc:
            print(f"[PERSIST] publish error: {exc!r}", flush=True)
        time.sleep(20)


def start_runtime_once():
    global _started
    with _lock:
        if _started:
            return
        persistent_runtime.ensure_schema()
        _safe_thread("asi-main-engine", worker_base.run_forever)
        _safe_thread("asi-premarket-h10", premarket_scheduler.run_forever)
        _safe_thread("asi-premarket-preliminary", premarket_preliminary.run_forever)
        _safe_thread("asi-persistent-publisher", _publisher_loop)
        _started = True


@api.on_event("startup")
def startup_event():
    start_runtime_once()


@api.get("/health")
def health():
    start_runtime_once()
    payload = persistent_runtime.local_snapshot(stock_app.DB)
    return {
        "ok": True,
        "service": "ai-stock-intelligence-runtime",
        "time": datetime.now(timezone.utc).isoformat(),
        "build_sha": payload.get("build_sha", ""),
        "threads": sorted(t.name for t in threading.enumerate() if t.name.startswith("asi-")),
        "premarket_status": ((payload.get("premarket") or {}).get("run") or {}).get("status"),
        "dynamic_count": len(payload.get("dynamic") or []),
        "snapshot_source": (payload.get("state") or {}).get("snapshot_source"),
    }


@api.get("/runtime")
def runtime():
    start_runtime_once()
    try:
        remote = persistent_runtime.read_remote()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"persistent store unavailable: {exc}")
    return remote or persistent_runtime.local_snapshot(stock_app.DB)


@api.post("/publish")
def publish_now():
    start_runtime_once()
    return persistent_runtime.publish(stock_app.DB)
