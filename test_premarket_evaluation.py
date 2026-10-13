import pandas as pd
from premarket_evaluation import evaluate_pick,summarize

def test_target_and_stop_outcomes():
    p={"ticker":"A","entry_low":100,"entry_high":102,"stop_loss":95,"target_1":110,"target_2":120}
    assert evaluate_pick(p,{"open":101,"high":112,"low":99,"close":108})["outcome"]=="TARGET_1"
    assert evaluate_pick(p,{"open":101,"high":104,"low":94,"close":96})["outcome"]=="STOP_LOSS"

def test_ambiguous_daily_bar_is_review():
    p={"ticker":"A","entry_low":100,"entry_high":102,"stop_loss":95,"target_1":110}
    r=evaluate_pick(p,{"open":101,"high":112,"low":94,"close":100})
    assert r["outcome"]=="AMBIGUOUS" and r["verdict"]=="REVIEW"

def test_summary_excludes_review_from_hit_rate():
    s=summarize(pd.DataFrame([{"verdict":"SESUAI"},{"verdict":"TIDAK_SESUAI"},{"verdict":"REVIEW"}]))
    assert s["hit_rate"]==0.5 and s["review"]==1
