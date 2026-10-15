from types import SimpleNamespace

import dynamic_engine
from ai_stock_ask import answer_stock
from premarket_evaluation import evaluate_pick


def _row(decision="BUY", mode="STRICT"):
    return SimpleNamespace(decision=decision, candidate_mode=mode)


def test_dynamic_monitor_fallback_can_never_be_entry_ready():
    state = dynamic_engine._state(
        _row("BUY", "MONITOR_FALLBACK"),
        {"entry_status": "ACTIVE"},
        99.0,
    )
    assert state == "MONITOR"


def test_every_sell_alias_is_blocked_from_entry_ready():
    for decision in ("SELL", "SELL/AVOID", "AVOID", "STRONG_SELL", "EXIT"):
        state = dynamic_engine._state(
            _row(decision, "STRICT"),
            {"entry_status": "ACTIVE"},
            99.0,
        )
        assert state == "MONITOR", decision


def test_ai_stock_ask_respects_monitor_only_even_if_underlying_buy_is_strong():
    result = answer_stock(
        {
            "ticker": "TEST",
            "decision": "BUY",
            "candidate_mode": "MONITOR_FALLBACK",
            "ai_score": 90,
            "jst_p_up": 0.80,
            "entry_status": "ACTIVE",
            "risk": "LOW",
            "market_regime": "BULLISH",
        },
        "TODAY",
    )
    assert result["action"] == "WAIT"
    assert result["candidate_mode"] == "MONITOR_FALLBACK"


def test_wait_premarket_pick_is_observation_not_trading_win():
    result = evaluate_pick(
        {
            "ticker": "TEST",
            "decision": "WAIT",
            "reference_price": 100,
            "entry_low": 99,
            "entry_high": 101,
            "stop_loss": 95,
            "target_1": 110,
        },
        {"open": 100, "high": 115, "low": 99, "close": 112},
    )
    assert result["target_1_hit"] is True
    assert result["outcome"] == "MONITOR_ONLY"
    assert result["verdict"] == "REVIEW"
