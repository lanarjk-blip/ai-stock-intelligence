"""Single-host API wrapper with deployment readiness semantics.

The canonical runtime_api keeps /health diagnostic and HTTP-200 friendly so the
UI can explain stale/warming engine states. The single-service runtime needs a
stricter Render health check: after cold-start grace, a missing/stale/stalled
worker must make the service unready so Render restarts worker and API together.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import HTTPException

import runtime_api

app = runtime_api.app
READY_VERSION = "RUNTIME-READY-R1"
_STARTED_MONOTONIC = time.monotonic()


def startup_grace_seconds(env: Dict[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    try:
        value = int(str(source.get("ASI_RUNTIME_STARTUP_GRACE_SECONDS") or "900"))
    except Exception:
        value = 900
    return max(60, min(value, 3600))


def readiness_decision(
    health_payload: Dict[str, Any] | None,
    *,
    elapsed_seconds: float,
    grace_seconds: int,
) -> Dict[str, Any]:
    payload = dict(health_payload or {})
    status = str(payload.get("status") or "RUNTIME_INVALID")
    engine = dict(payload.get("engine") or {})
    engine_status = str(engine.get("status") or "")
    ready = bool(payload.get("ok"))
    warming = status == "RUNTIME_MISSING" or engine_status in {
        "ENGINE_WARMING",
        "RUNTIME_ENGINE_WARMING",
    }
    within_grace = float(elapsed_seconds) < float(grace_seconds)
    grace_active = bool((not ready) and warming and within_grace)
    remaining = max(0, int(float(grace_seconds) - float(elapsed_seconds))) if grace_active else 0
    return {
        **payload,
        "ready": ready,
        "readiness_version": READY_VERSION,
        "startup_grace": grace_active,
        "startup_grace_seconds": int(grace_seconds),
        "startup_grace_remaining_seconds": remaining,
    }


@app.get("/ready")
def ready():
    grace = startup_grace_seconds()
    elapsed = max(0.0, time.monotonic() - _STARTED_MONOTONIC)
    try:
        health = runtime_api.health()
    except Exception as exc:
        # Cold database/API startup is allowed a bounded grace period. Do not
        # expose connection strings or exception text in this public endpoint.
        if elapsed < grace:
            return {
                "ready": False,
                "status": "RUNTIME_API_WARMING",
                "readiness_version": READY_VERSION,
                "startup_grace": True,
                "startup_grace_seconds": grace,
                "startup_grace_remaining_seconds": max(0, int(grace - elapsed)),
                "error_type": type(exc).__name__,
            }
        raise HTTPException(
            status_code=503,
            detail={
                "ready": False,
                "status": "RUNTIME_API_UNAVAILABLE",
                "readiness_version": READY_VERSION,
                "startup_grace": False,
                "error_type": type(exc).__name__,
            },
        ) from exc

    decision = readiness_decision(
        health,
        elapsed_seconds=elapsed,
        grace_seconds=grace,
    )
    if decision.get("ready") or decision.get("startup_grace"):
        return decision
    raise HTTPException(status_code=503, detail=decision)
