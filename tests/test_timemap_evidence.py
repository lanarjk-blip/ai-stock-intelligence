import copy
import json
import sqlite3
import pandas as pd
import pytest
from timemap_evidence import summarize,read_evidence
from timemap_forecast import MODEL_ID,schedule,predict
from timemap_forecast_ui import record,evaluate
from test_day_forecast import fixture
NOW=pd.Timestamp('2026-10-27T17:00:00+07:00')


def row(**changes):
    r=dict(ticker='BBCA',model_version=MODEL_ID,trading_date='2026-10-27',slot='2026-10-27T09:00:00+07:00',
        generated_at='2026-10-27T08:50:00+07:00',input_hash='original',feature_asof='2026-10-26T15:30:00+07:00',
        latest_candle='2026-10-26T16:00:00+07:00',training_end='2026-10-26',source='TEST FIXTURE',
        probabilities='[0.1,0.2,0.7]',actual_label=2,return_pct=1.,evaluated_at='2026-10-27T09:40:00+07:00',outcome_source='TEST FIXTURE')
    r.update(changes);return r


def test_first_prediction_is_scored_once_without_picking_a_winning_revision():
    first=row(probabilities='[0.7,0.2,0.1]')
    later=row(input_hash='later',generated_at='2026-10-27T08:55:00+07:00')
    a=summarize([later,first,first],'BBCA',NOW)
    assert a['n']==1 and a['correct']==0 and a['accuracy_pct']==0
    assert a['status']=='SAMPEL BELUM CUKUP' and not a['approved']


@pytest.mark.parametrize('change',[
    {'generated_at':'2026-10-26T08:50:00+07:00'},
    {'generated_at':'2026-10-27T09:01:00+07:00'},
    {'feature_asof':'2026-10-27T09:30:00+07:00'},
    {'latest_candle':'2026-10-27T09:30:00+07:00'},
    {'source':'LAST GOOD CACHE'}, {'training_end':'2026-10-27'},
    {'probabilities':'[0.9,0.9,0.9]'}, {'probabilities':'[NaN,0.1,0.9]'},
    {'evaluated_at':'2026-10-27T09:20:00+07:00'}, {'outcome_source':'UNKNOWN'},
    {'actual_label':0}, {'return_pct':float('nan')},
])
def test_unverifiable_evidence_is_not_counted_as_accuracy(change):
    r=summarize([row(**change)],'BBCA',NOW)
    assert r['n']==0 and r['accuracy_pct'] is None and r['rejected']==1


def test_incomplete_and_future_outcomes_wait_and_other_identities_are_excluded():
    r=summarize([row(evaluated_at='2026-10-28T09:40:00+07:00'),row(ticker='ANTM'),row(model_version='OLD')],'BBCA',NOW)
    assert r['n']==0 and r['pending']==1 and r['accuracy_pct'] is None
    r=summarize([row(actual_label=None)],'BBCA',NOW)
    assert r['pending']==1 and r['n']==0


def test_uniform_ties_are_not_reported_as_correct_down_predictions():
    r=summarize([row(probabilities=json.dumps([1/3]*3),actual_label=0,return_pct=-1)],'BBCA',NOW)
    assert r['n']==1 and r['direction_n']==0 and r['accuracy_pct'] is None
    assert r['brier']==pytest.approx(2/3)


def test_read_uses_actual_ledger_and_never_invents_results_for_another_ticker(tmp_path):
    path=tmp_path/'market.db';data=fixture()
    record(path,predict(data,'BBCA',pd.Timestamp('2026-10-27T08:50:00+07:00')))
    evaluate(path,'BBCA',data,NOW)
    r=read_evidence(path,'BBCA',NOW)
    assert r['n']==9 and r['pending']==1 and r['days']==1
    assert read_evidence(path,'ANTM',NOW)['n']==0
    # Outcomes evaluated after the supplied observation time cannot leak backwards.
    assert read_evidence(path,'BBCA',pd.Timestamp('2026-10-27T10:00:00+07:00'))['n']==0


def test_deterioration_is_reported_without_promoting_any_model():
    rows=[];days=pd.bdate_range(end=NOW.date(),periods=10,tz='Asia/Jakarta')
    for i,day in enumerate(days):
        for slot in schedule(day):
            if not slot['regular']:continue
            start=slot['start'];issued=day+pd.Timedelta(hours=8,minutes=50);prior=day-pd.Timedelta(days=1)
            rows.append(row(trading_date=str(day.date()),slot=start.isoformat(),generated_at=issued.isoformat(),
                input_hash=start.isoformat(),feature_asof=prior.isoformat(),latest_candle=prior.isoformat(),
                training_end=str(prior.date()),evaluated_at=(start+pd.Timedelta(minutes=35)).isoformat(),
                probabilities='[0.1,0.1,0.8]' if i<5 else '[0.8,0.1,0.1]'))
    r=summarize(rows,'BBCA',NOW)
    assert r['days']==10 and r['drift']=='PERFORMA MENURUN — PERLU DITINJAU'
    assert not r['approved']


def test_screen_displays_actual_sample_count_without_promising_accuracy(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import focused_timemap as focused
    import timemap_forecast_ui as ui
    import local_runtime_health
    from market_session import session_status
    path=tmp_path/'market.db';data=fixture()
    record(path,predict(data,'BBCA',pd.Timestamp('2026-10-27T08:50:00+07:00')))
    monkeypatch.setattr(ui,'DB_PATH',path)
    monkeypatch.setattr(focused,'load',lambda _:data)
    monkeypatch.setattr(focused,'session_status',lambda:session_status(now=NOW))
    monkeypatch.setattr(local_runtime_health,'request_worker_recovery',lambda:False)
    app=AppTest.from_string("from focused_timemap import render_selected\nrender_selected('BBCA','Fixture')").run(timeout=20)
    assert not app.exception and len(app.dataframe)==1
    text='\n'.join(x.value for x in app.caption)
    assert '9 hasil selesai' in text and 'SAMPEL BELUM CUKUP' in text
    assert 'bukan peluang trade berikutnya' in text and 'Status tetap NO EDGE' in text
    assert any('BELUM ADA PREDIKSI TERPERCAYA' in x.value for x in app.warning)
