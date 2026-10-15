from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

import runtime_api
import runtime_host_api


def test_readiness_ready_when_runtime_health_is_ready():
    out = runtime_host_api.readiness_decision(
        {"ok": True, "status": "RUNTIME_READY", "engine": {"status": "RUNTIME_READY"}},
        elapsed_seconds=120,
        grace_seconds=900,
    )
    assert out["ready"] is True
    assert out["engine_ready"] is True
    assert out["progressing_warmup"] is False
    assert out["startup_grace"] is False


def test_readiness_allows_only_missing_or_nonprogressing_warmup_during_startup_grace():
    missing = runtime_host_api.readiness_decision(
        {"ok": False, "status": "RUNTIME_MISSING", "engine": {"status": "ENGINE_WARMING"}},
        elapsed_seconds=120,
        grace_seconds=900,
    )
    assert missing["ready"] is False
    assert missing["startup_grace"] is True
    assert missing["startup_grace_remaining_seconds"] == 780

    stalled = runtime_host_api.readiness_decision(
        {"ok": False, "status": "ENGINE_STALLED", "engine": {"status": "ENGINE_STALLED"}},
        elapsed_seconds=120,
        grace_seconds=900,
    )
    assert stalled["ready"] is False
    assert stalled["startup_grace"] is False


def test_progressing_first_cycle_stays_deployment_ready_after_grace():
    out = runtime_host_api.readiness_decision(
        {
            "ok": False,
            "status": "ENGINE_WARMING",
            "heartbeat": {"worker_pid": 123},
            "engine": {
                "status": "ENGINE_WARMING",
                "engine_progress_age_seconds": 45,
                "engine_progress_max_age_seconds": 1200,
            },
        },
        elapsed_seconds=1800,
        grace_seconds=900,
    )
    assert out["engine_ready"] is False
    assert out["progressing_warmup"] is True
    assert out["ready"] is True
    assert out["startup_grace"] is False


def test_nonprogressing_warmup_after_grace_is_not_healthy():
    out = runtime_host_api.readiness_decision(
        {
            "ok": False,
            "status": "ENGINE_WARMING",
            "engine": {"status": "ENGINE_WARMING", "engine_progress_age_seconds": None},
        },
        elapsed_seconds=901,
        grace_seconds=900,
    )
    assert out["ready"] is False
    assert out["progressing_warmup"] is False
    assert out["startup_grace"] is False


def test_runtime_health_to_readiness_first_cycle_integration():
    now = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    state = {
        "engine_cycle_status": "RUNNING",
        "engine_last_run": "",
        "engine_progress_at": (now - timedelta(seconds=30)).isoformat(),
        "engine_progress_stage": "FULL_COLLECT",
    }
    cfg = {
        "runtime_heartbeat_max_age_seconds": 180,
        "engine_progress_max_age_seconds": 1200,
        "engine_result_max_age_minutes": 90,
    }
    heartbeat = (123, "gen", "build", now - timedelta(seconds=20), "bridge")
    health = runtime_api.runtime_health_snapshot(heartbeat, state, now=now, cfg=cfg)
    assert health["ok"] is False
    assert health["status"] == "ENGINE_WARMING"

    ready = runtime_host_api.readiness_decision(
        health,
        elapsed_seconds=1800,
        grace_seconds=900,
    )
    assert ready["engine_ready"] is False
    assert ready["progressing_warmup"] is True
    assert ready["ready"] is True


def test_ready_endpoint_returns_503_for_stalled_runtime(monkeypatch):
    monkeypatch.setattr(runtime_host_api, "_STARTED_MONOTONIC", 0.0)
    monkeypatch.setattr(runtime_host_api.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(runtime_host_api, "startup_grace_seconds", lambda env=None: 900)
    monkeypatch.setattr(
        runtime_host_api.runtime_api,
        "health",
        lambda: {"ok": False, "status": "ENGINE_STALLED", "engine": {"status": "ENGINE_STALLED"}},
    )
    try:
        runtime_host_api.ready()
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["status"] == "ENGINE_STALLED"


def test_startup_grace_is_bounded():
    assert runtime_host_api.startup_grace_seconds({"ASI_RUNTIME_STARTUP_GRACE_SECONDS": "1"}) == 60
    assert runtime_host_api.startup_grace_seconds({"ASI_RUNTIME_STARTUP_GRACE_SECONDS": "99999"}) == 3600
