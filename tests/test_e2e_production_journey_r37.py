import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import market_session
import premarket_scheduler
import runtime_api
import runtime_host_api

WIB = ZoneInfo("Asia/Jakarta")


def _wib(hour, minute):
    return datetime(2026, 10, 15, hour, minute, tzinfo=WIB)


def test_market_day_journey_h10_lock_to_regular_close(tmp_path, monkeypatch):
    """Exercise the user-visible Thursday journey with a real H-10 SQLite lock."""
    db = tmp_path / "market.db"
    monkeypatch.setattr(premarket_scheduler.app, "DB", str(db))

    state = {
        "data_quality_status": "READY",
        "data_quality_reasons": "",
        "last_collection": _wib(8, 45).astimezone(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(premarket_scheduler, "_state_get", lambda key, default="": state.get(key, default))
    monkeypatch.setattr(premarket_scheduler, "_state_set", lambda key, value: state.__setitem__(key, value))

    market_date = "2026-10-15"
    draft = {
        "version": premarket_scheduler.VERSION,
        "market_date": market_date,
        "generated_at": _wib(8, 49).astimezone(timezone.utc).isoformat(),
        "mode": "BUY_READY",
        "reason": "integration journey",
        "picks": [
            {
                "market_date": market_date,
                "rank": 1,
                "ticker": "BBCA",
                "company": "Bank Central Asia",
                "decision": "BUY",
                "confidence": 72.0,
                "ai_score": 80.0,
                "risk": "LOW",
                "reference_price": 10000.0,
                "entry_low": 9900.0,
                "entry_high": 10050.0,
                "stop_loss": 9700.0,
                "target_1": 10300.0,
                "target_2": 10600.0,
                "ihsg_regime": "BULLISH",
                "reason": "strict candidate",
                "source_phase": "journey",
            }
        ],
    }
    state["premarket_h10_draft_json"] = json.dumps(draft)
    state["premarket_h10_draft_market_date"] = market_date

    assert premarket_scheduler.window_state(_wib(8, 29)) == "BEFORE"
    assert premarket_scheduler.window_state(_wib(8, 30)) == "PREPARE"
    assert premarket_scheduler.window_state(_wib(8, 45)) == "LIGHT"
    assert premarket_scheduler.window_state(_wib(8, 50)) == "FINALIZE"

    first = premarket_scheduler.finalize_h10(_wib(8, 50))
    assert first["status"] == "LOCKED"
    assert first["market_date"] == market_date
    assert first["pick_count"] == 1
    assert first["timing"] == "ON_TIME"

    con = sqlite3.connect(db)
    run_before = con.execute(
        "SELECT status,pick_count,note FROM premarket_runs WHERE market_date=?",
        (market_date,),
    ).fetchone()
    pick_before = con.execute(
        "SELECT ticker,decision,reference_price FROM premarket_picks WHERE market_date=? AND rank=1",
        (market_date,),
    ).fetchone()
    con.close()
    assert run_before[0] == "LOCKED"
    assert run_before[1] == 1
    assert str(run_before[2]).startswith("H10_FINAL")
    assert pick_before == ("BBCA", "BUY", 10000.0)

    # A later daemon tick must not rewrite the final H-10 record.
    second = premarket_scheduler.finalize_h10(_wib(8, 55))
    assert second == {"status": "ALREADY_FINAL", "market_date": market_date}
    con = sqlite3.connect(db)
    run_after = con.execute(
        "SELECT status,pick_count,note FROM premarket_runs WHERE market_date=?",
        (market_date,),
    ).fetchone()
    pick_after = con.execute(
        "SELECT ticker,decision,reference_price FROM premarket_picks WHERE market_date=? AND rank=1",
        (market_date,),
    ).fetchone()
    con.close()
    assert run_after == run_before
    assert pick_after == pick_before

    assert premarket_scheduler.window_state(_wib(9, 0)) == "AFTER_OPEN"
    assert market_session.session_status(now=_wib(9, 0))["status"] == "SESSION_1"
    assert market_session.session_status(now=_wib(9, 0))["live"] is True
    assert market_session.session_status(now=_wib(12, 15))["status"] == "BREAK"
    assert market_session.session_status(now=_wib(13, 30))["status"] == "SESSION_2"
    assert market_session.session_status(now=_wib(13, 30))["live"] is True
    assert market_session.session_status(now=_wib(15, 50))["status"] == "CLOSED"
    assert market_session.session_status(now=_wib(15, 50))["live"] is False


def test_runtime_restart_journey_warming_to_ready_to_stale():
    """Model one external restart without allowing a dead runtime to look actionable."""
    cfg = {
        "runtime_heartbeat_max_age_seconds": 180,
        "engine_progress_max_age_seconds": 1200,
        "engine_result_max_age_minutes": 90,
    }
    now = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    heartbeat = (101, "restart-gen", "build-r37", now - timedelta(seconds=20), "bridge-r37")

    # Fresh heartbeat + fresh progress on the first long cycle: engine is not
    # actionable yet, but deployment stays alive beyond fixed startup grace.
    warming_state = {
        "engine_cycle_status": "RUNNING",
        "engine_last_run": "",
        "engine_progress_at": (now - timedelta(seconds=45)).isoformat(),
        "engine_progress_stage": "FULL_COLLECT",
    }
    warming = runtime_api.runtime_health_snapshot(heartbeat, warming_state, now=now, cfg=cfg)
    assert warming["ok"] is False
    assert warming["status"] == "ENGINE_WARMING"
    deploy = runtime_host_api.readiness_decision(warming, elapsed_seconds=1800, grace_seconds=900)
    assert deploy["engine_ready"] is False
    assert deploy["progressing_warmup"] is True
    assert deploy["ready"] is True

    # Once a full cycle completes, engine and deployment readiness converge.
    ready_state = {
        "engine_cycle_status": "IDLE",
        "engine_last_run": (now - timedelta(minutes=1)).isoformat(),
        "engine_progress_at": (now - timedelta(minutes=1)).isoformat(),
        "engine_progress_stage": "CYCLE_DONE",
    }
    healthy = runtime_api.runtime_health_snapshot(heartbeat, ready_state, now=now, cfg=cfg)
    assert healthy["ok"] is True
    assert healthy["status"] == "RUNTIME_READY"
    deploy = runtime_host_api.readiness_decision(healthy, elapsed_seconds=1900, grace_seconds=900)
    assert deploy["engine_ready"] is True
    assert deploy["ready"] is True

    # A stale worker heartbeat must fail closed even if the last result looked good.
    stale_heartbeat = (101, "restart-gen", "build-r37", now - timedelta(seconds=181), "bridge-r37")
    stale = runtime_api.runtime_health_snapshot(stale_heartbeat, ready_state, now=now, cfg=cfg)
    assert stale["ok"] is False
    assert stale["status"] == "RUNTIME_STALE"
    deploy = runtime_host_api.readiness_decision(stale, elapsed_seconds=2000, grace_seconds=900)
    assert deploy["ready"] is False
    assert deploy["startup_grace"] is False


def test_next_session_journey_after_close_weekend_and_holiday_gap():
    # Thursday after close targets Friday.
    assert market_session.next_trading_date(now=_wib(16, 0), after_close=True) == "2026-10-16"

    # Friday after close skips weekend and targets Monday.
    friday = datetime(2026, 10, 16, 16, 0, tzinfo=WIB)
    assert market_session.next_trading_date(now=friday, after_close=True) == "2026-10-19"

    # Verified Christmas closure jumps across the configured holiday block.
    dec23 = datetime(2026, 12, 23, 16, 0, tzinfo=WIB)
    assert market_session.next_trading_date(now=dec23, after_close=True) == "2026-12-28"
