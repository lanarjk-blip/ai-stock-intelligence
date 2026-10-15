"""Single-host API wrapper with deployment readiness semantics.

The canonical runtime_api keeps /health diagnostic and HTTP-200 friendly so the
UI can explain stale/warming engine states. The single-service runtime needs a
stricter Render health check: a missing/stale/stalled worker must eventually
make the service unready so Render restarts worker and API together, while a
cold first cycle that is still making fresh progress must not be restart-looped.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import HTTPException

import runtime_api

app = runtime_api.app
READY_VERSION = "RUNTIME-READY-R2"
_STARTED_MONOTONIC = time.monotonic()


def startup_grace_seconds(env: Dict[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    try:
        value = int(str(source.get("ASI_RUNTIME_STARTUP_GRACE_SECONDS") or "900"))
    except Exception:
        value = 900
    return max(60, min(value, 3600))


def _progressing_warmup(status: str, engine: Dict[str, Any]) -> bool:
    """True only for a live worker whose first cycle is still making progress."""
    if status not in {"ENGINE_WARMING", "RUNTIME_ENGINE_WARMING"}:
        return False
    engine_status = str(engine.get("status") or "")
    if engine_status not in {"ENGINE_WARMING", "RUNTIME_ENGINE_WARMING"}:
        return False
    try:
        age = float(engine.get("engine_progress_age_seconds"))
        limit = float(engine.get("engine_progress_max_age_seconds"))
    except (TypeError, ValueError):
        return False
    return age >= 0 and limit > 0 and age <= limit


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
    engine_ready = bool(payload.get("ok"))
    progress_warmup = bool((not engine_ready) and _progressing_warmup(status, engine))
    deployment_ready = bool(engine_ready or progress_warmup)

    warming = status == "RUNTIME_MISSING" or engine_status in {
        "ENGINE_WARMING",
        "RUNTIME_ENGINE_WARMING",
    }
    within_grace = float(elapsed_seconds) < float(grace_seconds)
    grace_active = bool((not deployment_ready) and warming and within_grace)
    remaining = max(0, int(float(grace_seconds) - float(elapsed_seconds))) if grace_active else 0
    return {
        **payload,
        "engine_ready": engine_ready,
        "ready": deployment_ready,
        "progressing_warmup": progress_warmup,
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
                "engine_ready": False,
                "progressing_warmup": False,
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
                "engine_ready": False,
                "progressing_warmup": False,
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
