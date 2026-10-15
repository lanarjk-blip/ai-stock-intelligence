from fastapi import HTTPException

import runtime_host_api


def test_readiness_ready_when_runtime_health_is_ready():
    out = runtime_host_api.readiness_decision(
        {"ok": True, "status": "RUNTIME_READY", "engine": {"status": "RUNTIME_READY"}},
        elapsed_seconds=120,
        grace_seconds=900,
    )
    assert out["ready"] is True
    assert out["startup_grace"] is False


def test_readiness_allows_only_warming_or_missing_during_startup_grace():
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


def test_readiness_missing_after_grace_is_not_healthy():
    out = runtime_host_api.readiness_decision(
        {"ok": False, "status": "RUNTIME_MISSING", "engine": {"status": "ENGINE_WARMING"}},
        elapsed_seconds=901,
        grace_seconds=900,
    )
    assert out["ready"] is False
    assert out["startup_grace"] is False


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
