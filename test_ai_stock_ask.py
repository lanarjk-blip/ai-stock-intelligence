from ai_stock_ask import answer_stock


def test_buy_when_strong_and_entry_active():
    r=answer_stock({"ticker":"TEST","decision":"BUY","ai_score":80,"jst_p_up":.70,"entry_status":"ACTIVE","risk":"MEDIUM","market_regime":"BULLISH","entry_low":100,"entry_high":102,"stop_loss":95,"target_1":110},"TODAY")
    assert r["action"]=="BUY"
    assert r["levels"]["entry_low"]==100


def test_wait_when_entry_not_confirmed():
    r=answer_stock({"ticker":"TEST","decision":"BUY","ai_score":80,"jst_p_up":.70,"entry_status":"WAIT","risk":"LOW"},"TODAY")
    assert r["action"]=="WAIT"


def test_monitor_fallback_never_becomes_buy():
    r=answer_stock({
        "ticker":"TEST","decision":"BUY","candidate_mode":"MONITOR_FALLBACK",
        "ai_score":90,"jst_p_up":.80,"entry_status":"ACTIVE","risk":"LOW",
        "market_regime":"BULLISH"
    },"TODAY")
    assert r["action"]=="WAIT"
    assert r["candidate_mode"]=="MONITOR_FALLBACK"
    assert "MONITOR" in r["reason"]


def test_avoid_high_risk_or_bearish_tomorrow():
    assert answer_stock({"ticker":"A","decision":"BUY","ai_score":90,"jst_p_up":.8,"risk":"HIGH"},"TODAY")["action"]=="AVOID"
    assert answer_stock({"ticker":"B","decision":"BUY","ai_score":90,"jst_p_up":.8,"risk":"LOW","market_regime":"BEARISH"},"TOMORROW")["action"]=="AVOID"


def test_missing_ticker_never_invents_answer():
    assert answer_stock({},"TODAY")["status"]=="NO_DATA"
