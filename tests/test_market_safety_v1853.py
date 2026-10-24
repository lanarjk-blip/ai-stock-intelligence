import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def test_bundled_and_configured_2026_idx_holidays_are_identical():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    calendar = json.loads(Path("data/idx_holidays.json").read_text(encoding="utf-8"))
    bundled = set(calendar["holidays"]["2026"])
    configured = set(cfg["market_holidays"])
    assert 2026 in calendar["verified_years"]
    assert cfg["market_calendar_verified_years"] == [2026]
    assert bundled == configured
    assert len(bundled) == 22
    assert "2026-12-24" in bundled
    assert "2026-12-25" in bundled
    assert "2026-12-31" in bundled


def test_market_session_respects_verified_long_holiday_gap():
    import market_session

    wib = ZoneInfo("Asia/Jakarta")
    holiday = datetime(2026, 12, 24, 10, 0, tzinfo=wib)
    status = market_session.session_status(now=holiday)
    assert status["status"] == "HOLIDAY"
    assert status["live"] is False
    assert status["calendar_verified"] is True

    after_close = datetime(2026, 12, 23, 16, 10, tzinfo=wib)
    assert market_session.next_trading_date(now=after_close, after_close=True) == "2026-12-28"


def test_premarket_scheduler_uses_configured_verified_holidays():
    import premarket_scheduler

    wib = ZoneInfo("Asia/Jakarta")
    holiday_morning = datetime(2026, 12, 24, 8, 40, tzinfo=wib)
    assert "2026-12-24" in set(premarket_scheduler.CFG.get("market_holidays") or [])
    assert premarket_scheduler.window_state(holiday_morning) == "OFF"
    assert premarket_scheduler._target_market_date(holiday_morning) == "2026-12-28"


def _write_remote_state(db, heartbeat, *, engine_last_run=None, engine_status=None, engine_progress_at=None):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE state (key TEXT PRIMARY KEY,value TEXT)")
    rows = {
        "remote_worker_runtime_build": "R21+unit",
        "remote_worker_generation": "gen-unit",
        "remote_worker_pid": "123",
        "remote_worker_heartbeat_at": heartbeat,
        "remote_worker_bridge_version": "unit",
    }
    if engine_last_run is not None:
        rows["engine_last_run"] = engine_last_run
    if engine_status is not None:
        rows["engine_cycle_status"] = engine_status
    if engine_progress_at is not None:
        rows["engine_progress_at"] = engine_progress_at
        rows["engine_progress_stage"] = "CYCLE_DONE" if engine_status == "IDLE" else "COLLECT_PROGRESS"
    con.executemany("INSERT INTO state(key,value) VALUES(?,?)", rows.items())
    con.commit()
    con.close()


def test_external_runtime_fresh_heartbeat_and_engine_result_are_actionable(tmp_path, monkeypatch):
    import runtime_health

    db = tmp_path / "market.db"
    _write_remote_state(
        db,
        "2026-10-15T00:00:00+00:00",
        engine_last_run="2026-10-15T00:01:00+00:00",
        engine_status="IDLE",
        engine_progress_at="2026-10-15T00:01:00+00:00",
    )
    monkeypatch.setattr(runtime_health, "DB", str(db))
    monkeypatch.setattr(runtime_health, "external_runtime_expected", lambda: True)
    monkeypatch.setattr(runtime_health, "_config", lambda: {
        "runtime_heartbeat_max_age_seconds": 180,
        "engine_progress_max_age_seconds": 1200,
        "engine_result_max_age_minutes": 90,
    })

    now = datetime(2026, 10, 15, 0, 2, tzinfo=timezone.utc)
    health = runtime_health.remote_runtime_health(now=now)
    assert health["required"] is True
    assert health["ready"] is True
    assert health["status"] == "RUNTIME_READY"
    assert health["age_seconds"] == 120
    assert health["engine_last_run_age_seconds"] == 60


def test_external_runtime_fresh_heartbeat_without_engine_result_is_warming(tmp_path, monkeypatch):
    import runtime_health

    db = tmp_path / "market.db"
    _write_remote_state(db, "2026-10-15T00:00:00+00:00")
    monkeypatch.setattr(runtime_health, "DB", str(db))
    monkeypatch.setattr(runtime_health, "external_runtime_expected", lambda: True)
    monkeypatch.setattr(runtime_health, "_config", lambda: {
        "runtime_heartbeat_max_age_seconds": 180,
        "engine_progress_max_age_seconds": 1200,
        "engine_result_max_age_minutes": 90,
    })
    now = datetime(2026, 10, 15, 0, 2, tzinfo=timezone.utc)
    health = runtime_health.remote_runtime_health(now=now)
    assert health["ready"] is False
    assert health["status"] == "RUNTIME_ENGINE_WARMING"


def test_external_runtime_stale_or_missing_heartbeat_blocks_actionable(tmp_path, monkeypatch):
    import runtime_health

    db = tmp_path / "market.db"
    _write_remote_state(db, "2026-10-15T00:00:00+00:00")
    monkeypatch.setattr(runtime_health, "DB", str(db))
    monkeypatch.setattr(runtime_health, "external_runtime_expected", lambda: True)
    monkeypatch.setattr(runtime_health, "_config", lambda: {"runtime_heartbeat_max_age_seconds": 180})

    now = datetime(2026, 10, 15, 0, 10, tzinfo=timezone.utc)
    health = runtime_health.remote_runtime_health(now=now)
    assert health["ready"] is False
    assert health["status"] == "RUNTIME_STALE"
    assert health["age_seconds"] == 600
    assert "> 180s" in health["reason"]

    empty = tmp_path / "empty.db"
    con = sqlite3.connect(empty)
    con.execute("CREATE TABLE state (key TEXT PRIMARY KEY,value TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(runtime_health, "DB", str(empty))
    missing = runtime_health.remote_runtime_health(now=now)
    assert missing["ready"] is False
    assert missing["status"] == "RUNTIME_MISSING"


def test_ui_safety_patch_blocks_dynamic_and_premarket_on_stale_runtime():
    source = Path("runtime_health_ui_patch.py").read_text(encoding="utf-8")
    assert "dynamic_opportunity_ui._quality_gate_state = quality_gate_state" in source
    assert "premarket_ui_patch._ensure_premarket_on_open = ensure_premarket_on_open" in source
    assert "RUNTIME TIDAK SEGAR" in source
    assert "health.get(\"required\") and not health.get(\"ready\")" in source


def test_sitecustomize_installs_runtime_health_patch_and_watches_calendar():
    source = Path("sitecustomize.py").read_text(encoding="utf-8")
    assert "runtime_health_ui_patch.install()" in source
    assert '"runtime_health.py"' in source
    assert '"runtime_health_ui_patch.py"' in source
    assert '"data/idx_holidays.json"' in source
