import pandas as pd
from premarket_intelligence import select_high_conviction


def test_wait_watch_never_leak_when_no_buy():
    df = pd.DataFrame([
        {"ticker":"AAAA","decision":"WAIT","ai_score":90,"jst_p_up":0.80},
        {"ticker":"BBBB","decision":"WATCH","ai_score":95,"jst_p_up":0.85},
    ])
    out = select_high_conviction(df, {})
    assert out["status"] == "NO_TRADE"
    assert out["picks"].empty


def test_only_buy_rows_that_pass_all_gates_survive():
    df = pd.DataFrame([
        {"ticker":"GOOD","decision":"BUY","ai_score":75,"jst_p_up":0.70,"risk":"MEDIUM"},
        {"ticker":"LOWAI","decision":"BUY","ai_score":50,"jst_p_up":0.80,"risk":"LOW"},
        {"ticker":"LOWP","decision":"STRONG_BUY","ai_score":80,"jst_p_up":0.40,"risk":"LOW"},
        {"ticker":"WAIT","decision":"WAIT","ai_score":99,"jst_p_up":0.99,"risk":"LOW"},
    ])
    out = select_high_conviction(df, {"premarket_min_ai_score":60,"premarket_min_p_up":0.55})
    assert out["status"] == "LOCKED"
    assert out["picks"]["ticker"].tolist() == ["GOOD"]


def test_list_is_not_padded_to_five():
    df = pd.DataFrame([
        {"ticker":"ONE","decision":"BUY","ai_score":81,"jst_p_up":0.71},
        {"ticker":"TWO","decision":"STRONG_BUY","ai_score":83,"jst_p_up":0.73},
        {"ticker":"WAIT","decision":"WAIT","ai_score":99,"jst_p_up":0.99},
    ])
    out = select_high_conviction(df, {"premarket_max_picks":5})
    assert len(out["picks"]) == 2
    assert set(out["picks"]["ticker"]) == {"ONE","TWO"}


def test_percent_probability_is_normalized():
    df = pd.DataFrame([
        {"ticker":"PASS","decision":"BUY","ai_score":70,"jst_p_up":70},
        {"ticker":"FAIL","decision":"BUY","ai_score":70,"jst_p_up":40},
    ])
    out = select_high_conviction(df, {"premarket_min_p_up":0.55})
    assert out["picks"]["ticker"].tolist() == ["PASS"]
