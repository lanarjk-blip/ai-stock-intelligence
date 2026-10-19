from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import live_precision_patch as lp
import ui_theme_patch


def _metrics(ret, mom, rs=0.0, vol=1.0, pos=0.5, fresh=True, live=True):
    return {
        "live_required": live,
        "fresh": fresh,
        "return_from_open_pct": ret,
        "momentum_pct": mom,
        "relative_strength_pct": rs,
        "volume_ratio": vol,
        "range_position": pos,
    }


def test_green_strength_scores_above_red_tape():
    row = SimpleNamespace(ticker="AAAA")
    breadth = {"defensive": False}
    green = lp.score_adjustment(row, _metrics(2.0, 0.8, 1.5, 1.6, 0.8), breadth)
    red = lp.score_adjustment(row, _metrics(-2.0, -0.8, -1.5, 1.6, 0.2), breadth)
    assert green > 0
    assert red < 0
    assert green > red


def test_defensive_market_rewards_relative_green_and_penalizes_red():
    row = SimpleNamespace(ticker="AAAA")
    breadth = {"defensive": True}
    green = lp.score_adjustment(row, _metrics(1.2, 0.3, 2.0, 1.4, 0.8), breadth)
    red = lp.score_adjustment(row, _metrics(-0.7, -0.2, -1.2, 1.2, 0.3), breadth)
    assert green > red


def test_time_map_live_match_and_divergence():
    match, score = lp.map_live_match(68, 12, _metrics(1.8, 0.6, 1.2))
    assert match in {"MATCH", "MATCH STRONG"}
    assert score >= 55
    divergent, divergent_score = lp.map_live_match(68, 12, _metrics(-1.8, -0.6, -1.2))
    assert divergent == "DIVERGENCE"
    assert divergent_score < score


def test_live_guard_blocks_high_risk_and_negative_momentum():
    high_risk = SimpleNamespace(ticker="AAAA", risk="HIGH")
    assert lp.guard_state(high_risk, {"live_match": "MATCH"}, 85, "ENTRY READY", _metrics(2, 0.5, 1.5), {"defensive": False}) == "MONITOR"
    normal = SimpleNamespace(ticker="AAAA", risk="MEDIUM")
    assert lp.guard_state(normal, {"live_match": "MATCH"}, 85, "ENTRY READY", _metrics(-1.2, -0.2, -0.8), {"defensive": False}) == "MONITOR"


def test_stale_live_data_blocks_entry_ready():
    row = SimpleNamespace(ticker="AAAA", risk="LOW")
    assert lp.guard_state(row, {"live_match": "MIXED"}, 90, "ENTRY READY", _metrics(1, 0.2, 1, fresh=False), {"defensive": False}) == "MONITOR"


def test_priority_sort_prefers_live_relative_strength():
    frame = pd.DataFrame([
        {"ticker": "RED", "rel_ret1": -0.03, "ret1": -0.02, "vol_ratio": 1.0, "relative_rank_score": 90, "ai_score": 90, "jst_p_up": 0.8},
        {"ticker": "GREEN", "rel_ret1": 0.05, "ret1": 0.04, "vol_ratio": 1.8, "relative_rank_score": 75, "ai_score": 72, "jst_p_up": 0.66},
    ])
    ranked = lp.priority_sort_frame(frame)
    assert ranked.iloc[0]["ticker"] == "GREEN"


def test_time_map_semantics_are_font_only():
    up = ui_theme_patch._direction_style("UP %", 65)
    down = ui_theme_patch._direction_style("DOWN %", 55)
    sideways = ui_theme_patch._direction_style("Sideways %", 50)
    assert "color:#15803d" in up
    assert "color:#b91c1c" in down
    assert "color:#b45309" in sideways
    assert "background" not in up.lower()
    assert "background" not in down.lower()
    assert "background" not in sideways.lower()


def test_time_map_missing_live_data_is_explicit():
    status, score = lp.map_live_match(65, 15, _metrics(0, 0, fresh=False))
    assert status == "DATA INTRADAY BELUM CUKUP"
    assert score is None


def test_off_market_adjustment_does_not_distort_premarket():
    row = SimpleNamespace(ticker="AAAA")
    assert lp.score_adjustment(row, _metrics(0, 0, live=False), {"defensive": False}) == 0.0


def test_defensive_guard_demotes_weak_entry():
    row = SimpleNamespace(ticker="AAAA", risk="MEDIUM")
    state = lp.guard_state(
        row,
        {"live_match": "MATCH"},
        74,
        "ENTRY READY",
        _metrics(0.1, 0.0, 0.0),
        {"defensive": True},
    )
    assert state == "DEVELOPING"
