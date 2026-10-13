import pandas as pd
from closing_overnight import select_overnight


def cfg():
    return {"overnight_max_picks":5,"overnight_min_ai_score":75,"overnight_min_p_up":0.64,"overnight_min_risk_reward":1.5,"overnight_max_risk":"MEDIUM","overnight_block_bearish_ihsg":True}

def test_no_trade_when_no_buy():
    r=select_overnight(pd.DataFrame([{"decision":"WATCH","ai_score":90,"jst_p_up":.9}]),cfg())
    assert r["status"]=="NO_OVERNIGHT_TRADE"
    assert r["picks"].empty

def test_strict_filters_and_no_padding():
    df=pd.DataFrame([
      {"ticker":"GOOD","decision":"BUY","ai_score":82,"jst_p_up":.70,"risk_reward_2":2.0,"risk":"MEDIUM","market_regime":"BULLISH"},
      {"ticker":"RISKY","decision":"BUY","ai_score":90,"jst_p_up":.80,"risk_reward_2":3.0,"risk":"HIGH","market_regime":"BULLISH"},
      {"ticker":"BEAR","decision":"BUY","ai_score":90,"jst_p_up":.80,"risk_reward_2":3.0,"risk":"LOW","market_regime":"BEARISH"},
    ])
    r=select_overnight(df,cfg())
    assert r["status"]=="OVERNIGHT_READY"
    assert list(r["picks"]["ticker"])==["GOOD"]
    assert len(r["picks"])==1

def test_percent_probability_is_supported():
    df=pd.DataFrame([{"ticker":"PCT","decision":"STRONG_BUY","ai_score":80,"jst_p_up":70,"risk_reward_2":1.8,"risk":"LOW","market_regime":"NEUTRAL"}])
    assert select_overnight(df,cfg())["status"]=="OVERNIGHT_READY"
