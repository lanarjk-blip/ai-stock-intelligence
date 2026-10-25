import numpy as np
import pandas as pd
import timemap_statistics as stats


def test_partial_closing_and_nontrading_prices_do_not_count_as_evidence():
    stamps=pd.to_datetime(['2026-10-22 09:00','2026-10-22 09:30','2026-10-22 10:00',
                           '2026-10-22 15:00','2026-10-22 15:30','2026-10-22 09:15']).tz_localize('Asia/Jakarta')
    frame=pd.DataFrame(dict(ts=stamps,open=100,high=101,low=99,close=100,volume=[10,0,np.nan,10,10,10]))
    obs=stats.observations(frame,'TEST',pd.Timestamp('2026-10-23',tz='Asia/Jakarta'))
    assert list(obs.slot)==['09:00','15:00']
    table=stats.build('TEST',frame,pd.Timestamp('2026-10-23',tz='Asia/Jakarta')).set_index('time')
    assert table.loc['15:30','horizon']=='PARTIAL SESSION'
    assert pd.isna(table.loc['15:30','up_pct'])


def test_friday_lunch_boundary_and_full_morning_bar():
    assert stats.full_regular_bar(pd.Timestamp('2026-10-23 11:00',tz='Asia/Jakarta'))
    assert not stats.full_regular_bar(pd.Timestamp('2026-10-23 11:30',tz='Asia/Jakarta'))
    assert not stats.full_regular_bar(pd.Timestamp('2026-10-23 13:30',tz='Asia/Jakarta'))
    assert stats.full_regular_bar(pd.Timestamp('2026-10-23 14:00',tz='Asia/Jakarta'))


def sample():
    days=pd.date_range('2026-08-01',periods=40).date
    return pd.DataFrame([dict(day=d,slot=s,ret=.01) for d in days for s in ['09:00','09:30']])


def test_imbalanced_market_cannot_pass_by_beating_uniform_only():
    summary,ev=stats.walk_forward(sample())
    assert summary['brier']<2/3
    assert summary['baseline_brier']<summary['brier']
    assert summary['status']=='NO EDGE VS BASELINES'
    assert summary['evaluation_days']==32 and 0<=summary['ece']<=1
    assert (ev.train_end<ev.day).all()


def test_future_and_same_day_outcomes_cannot_change_probabilities():
    obs=sample(); _,before=stats.walk_forward(obs)
    boundary=obs.day.unique()[25]
    obs.loc[obs.day>=boundary,'ret']=-.01
    _,after=stats.walk_forward(obs)
    columns=['day','slot','train_end','p_down','p_flat','p_up']
    pd.testing.assert_frame_equal(before.loc[before.day<=str(boundary),columns],after.loc[after.day<=str(boundary),columns])
    # Exact duplicate bars cannot increase the effective training count.
    _,duplicated=stats.walk_forward(pd.concat([obs,obs]))
    pd.testing.assert_frame_equal(after,duplicated)
