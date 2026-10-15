import pandas as pd
from premarket_evaluation import evaluate_pick,summarize


def test_target_and_stop_outcomes():
    p={"ticker":"A","decision":"BUY","entry_low":100,"entry_high":102,"stop_loss":95,"target_1":110,"target_2":120}
    assert evaluate_pick(p,{"open":101,"high":112,"low":99,"close":108})["outcome"]=="TARGET_1"
    assert evaluate_pick(p,{"open":101,"high":104,"low":94,"close":96})["outcome"]=="STOP_LOSS"


def test_ambiguous_daily_bar_is_review():
    p={"ticker":"A","decision":"BUY","entry_low":100,"entry_high":102,"stop_loss":95,"target_1":110}
    r=evaluate_pick(p,{"open":101,"high":112,"low":94,"close":100})
    assert r["outcome"]=="AMBIGUOUS" and r["verdict"]=="REVIEW"


def test_monitor_only_never_counts_as_trading_win():
    p={"ticker":"A","decision":"WAIT","reference_price":100,"entry_low":99,"entry_high":101,"stop_loss":95,"target_1":110}
    r=evaluate_pick(p,{"open":100,"high":115,"low":99,"close":112})
    assert r["entry_touched"] is True
    assert r["target_1_hit"] is True
    assert r["outcome"]=="MONITOR_ONLY"
    assert r["verdict"]=="REVIEW"
    assert r["actionable"] is False


def test_reference_price_is_used_when_entry_zone_missing():
    p={"ticker":"A","decision":"BUY","reference_price":100,"target_1":110}
    r=evaluate_pick(p,{"open":100,"high":111,"low":99,"close":108})
    assert r["entry_touched"] is True and r["outcome"]=="TARGET_1"


def test_summary_excludes_review_from_hit_rate():
    s=summarize(pd.DataFrame([{"verdict":"SESUAI"},{"verdict":"TIDAK_SESUAI"},{"verdict":"REVIEW"}]))
    assert s["hit_rate"]==0.5 and s["review"]==1
