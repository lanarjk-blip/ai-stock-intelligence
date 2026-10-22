import sqlite3

import pandas as pd

import research_feedback as rf


def _frame(returns, p_up=0.70):
    return pd.DataFrame({
        "outcome_return_pct": list(returns),
        "p_up_norm": [p_up] * len(returns),
    })


def test_summary_includes_cost_calibration_profit_factor_and_drawdown():
    d = _frame([4.0, 3.0, -2.0, -1.0], p_up=0.75)
    m = rf.summarize_outcomes(d, roundtrip_cost_pct=0.5)
    assert m["samples"] == 4
    assert round(m["win_rate"], 4) == 0.5
    assert round(m["predicted_up"], 4) == 0.75
    assert round(m["calibration_gap"], 4) == 0.25
    assert round(m["net_expectancy"], 4) == 0.5
    assert m["profit_factor"] > 1.0
    assert m["max_drawdown"] < 0


def test_insufficient_sample_is_fail_open(monkeypatch):
    monkeypatch.setattr(rf, "regime_feedback", lambda regime: {
        "ready": False, "samples": 4, "net_expectancy": -9.0,
        "profit_factor": 0.1, "calibration_gap": 0.5, "max_drawdown": -30.0,
    })
    adj, metrics = rf.feedback_adjustment(pd.Series({"market_regime": "BEARISH"}))
    assert adj == 0.0
    assert rf.quality_block(metrics) is False


def test_bad_sample_penalises_and_blocks_entry_ready(monkeypatch):
    bad = {
        "ready": True, "samples": 30, "net_expectancy": -1.2,
        "profit_factor": 0.55, "calibration_gap": 0.28, "max_drawdown": -20.0,
    }
    monkeypatch.setattr(rf, "regime_feedback", lambda regime: bad)
    adj, _ = rf.feedback_adjustment(pd.Series({"market_regime": "BEARISH"}))
    assert adj == -15.0
    assert rf.quality_block(bad) is True

    monkeypatch.setattr(rf, "_ORIGINAL_STATE", lambda row, ts, score: "ENTRY READY")
    assert rf._patched_state(pd.Series({"market_regime": "BEARISH"}), {}, 80.0) == "DEVELOPING"


def test_good_sample_can_only_earn_small_reward(monkeypatch):
    good = {
        "ready": True, "samples": 40, "net_expectancy": 1.1,
        "profit_factor": 1.7, "calibration_gap": 0.05, "max_drawdown": -6.0,
    }
    monkeypatch.setattr(rf, "regime_feedback", lambda regime: good)
    adj, _ = rf.feedback_adjustment(pd.Series({"market_regime": "BULLISH"}))
    assert adj == 3.0
    assert rf.quality_block(good) is False


def test_regime_feedback_prefers_same_regime_when_sample_is_sufficient(tmp_path, monkeypatch):
    db = tmp_path / "market.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE recommendation_history (
            id INTEGER PRIMARY KEY,
            market_date TEXT,
            ticker TEXT,
            market_regime TEXT,
            p_up REAL,
            confidence REAL,
            outcome_status TEXT,
            outcome_return_pct REAL,
            actionable_at_record INTEGER
        )
    """)
    rows = []
    for i in range(20):
        rows.append((i + 1, "2026-10-01", f"B{i}", "BEARISH", 0.8, 0.8, "STOP", -2.0, 1))
    for i in range(20):
        rows.append((100 + i, "2026-10-01", f"U{i}", "BULLISH", 0.6, 0.7, "TP2", 3.0, 1))
    con.executemany("INSERT INTO recommendation_history VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    monkeypatch.setattr(rf, "_dbcon", lambda: sqlite3.connect(db))
    monkeypatch.setattr(rf, "_cfg", lambda: {
        "research_feedback_min_regime_samples": 20,
        "research_feedback_min_global_samples": 30,
        "research_roundtrip_cost_pct": 0.35,
        "research_feedback_cache_seconds": 60,
    })
    rf._CACHE.update({"at": 0.0, "rows": None})
    m = rf.regime_feedback("BEARISH", force=True)
    assert m["ready"] is True
    assert m["scope"] == "REGIME"
    assert m["samples"] == 20
    assert m["net_expectancy"] < 0
