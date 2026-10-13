from premarket_explain import explain_pick


def test_explanation_uses_actual_factors_and_opening_gate():
    text = explain_pick({
        "decision": "BUY", "ai_score": 81, "jst_p_up": 0.71,
        "relative_percentile": 0.94, "risk_reward_2": 2.1,
        "risk": "MEDIUM", "market_regime": "BULLISH",
    })
    assert "SEBAB:" in text
    assert "AI Score 81.0" in text
    assert "P(UP) 71.0%" in text
    assert "relative strength 94.0%" in text
    assert "R/R 2.10" in text
    assert "risk MEDIUM" in text
    assert "IHSG BULLISH" in text
    assert "AKIBAT: kandidat BUY Pre-Market" in text
    assert "Opening Confirmation" in text


def test_explanation_does_not_invent_missing_factors():
    text = explain_pick({"decision": "STRONG_BUY", "ai_score": 75})
    assert "AI Score 75.0" in text
    assert "P(UP)" not in text
    assert "R/R" not in text
    assert "IHSG" not in text
