import copy
import json
import sqlite3
import numpy as np
import pandas as pd
import pytest
from timemap_forecast import predict,schedule,prepare,VERSION
from timemap_forecast_ui import record,evaluate,saved,table,prospective_summary,percentages
from market_session import session_status


def fixture(ticker='BBCA'):
    rng=np.random.default_rng(91);rows=[]
    for day in pd.date_range('2026-09-01','2026-10-27',freq='B',tz='Asia/Jakarta'):
        if not session_status(now=day+pd.Timedelta(hours=9,minutes=30))['live']:continue
        for slot in schedule(day):
            if not slot['regular']:continue
            opening=100+rng.normal(0,2)
            closing=opening*(1+rng.normal(0,.004))
            rows.append(dict(ts=slot['start'],open=opening,high=max(opening,closing)+1,
                             low=min(opening,closing)-1,close=closing,volume=float(rng.integers(100,10000))))
    data=pd.DataFrame(rows);data.attrs={'ticker':ticker,'source':'TEST FIXTURE'}
    return data


def test_full_day_premarket_forecast_has_real_probabilities_for_all_regular_slots():
    r=predict(fixture(),'BBCA',pd.Timestamp('2026-10-27T08:50:00+07:00'))
    eligible=[x for x in r['rows'] if x['p'] is not None]
    assert len(eligible)==10 and not r['actionable']
    for row in eligible:
        assert sum(row['p'])==pytest.approx(1) and min(row['p'])>=0 and row['n']>=12
        assert pd.Timestamp(row['start'])>=pd.Timestamp(r['generated_at'])
    assert pd.Timestamp(r['training_end']).date()<pd.Timestamp(r['generated_at']).date()
    assert 'PRE-MARKET' in r['phase']


def test_current_or_future_target_closes_cannot_leak_into_forecast():
    d=fixture();now=pd.Timestamp('2026-10-27T09:30:00+07:00')
    a=predict(d,'BBCA',now)
    changed=d.copy();mask=pd.to_datetime(changed.ts,utc=True)>=now.tz_convert('UTC')
    changed.loc[mask,'close']*=1.2;changed.loc[mask,'high']*=1.3
    b=predict(changed,'BBCA',now)
    assert a['input_hash']==b['input_hash']
    assert [r['p'] for r in a['rows']]==[r['p'] for r in b['rows']]


def test_completed_today_prefix_changes_forecasts_not_just_dates():
    d=fixture();now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    a=predict(d,'BBCA',now)
    changed=d.copy();mask=pd.to_datetime(changed.ts,utc=True)==pd.Timestamp('2026-10-27T09:30:00+07:00').tz_convert('UTC')
    changed.loc[mask,'close']*=1.05;changed.loc[mask,'high']*=1.1
    b=predict(changed,'BBCA',now)
    assert a['input_hash']!=b['input_hash']
    assert any(x['p']!=y['p'] for x,y in zip(a['rows'],b['rows']) if x['p'] is not None)


@pytest.mark.parametrize('now',['2026-10-23T08:50:00+07:00','2026-12-24T08:50:00+07:00','2027-01-04T08:50:00+07:00'])
def test_calendar_friday_holiday_and_year_rollover(now):
    plan=schedule(pd.Timestamp(now))
    if '10-23' in now:
        assert not next(x for x in plan if x['start'].strftime('%H:%M')=='13:30')['regular']
        assert next(x for x in plan if x['start'].strftime('%H:%M')=='14:00')['regular']
        assert not next(x for x in plan if x['start'].strftime('%H:%M')=='11:30')['regular']
    elif '12-24' in now:assert not any(x['regular'] for x in plan)
    else:assert any(x['regular'] for x in plan)


@pytest.mark.parametrize('fault',['ticker','fallback','yesterday','few_days','unknown_source'])
def test_bad_data_never_creates_forecast_numbers(fault):
    d=fixture();now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    if fault=='ticker':d.attrs['ticker']='MPXL'
    elif fault=='fallback':d.attrs['fallback']=True
    elif fault=='yesterday':d=d[pd.to_datetime(d.ts,utc=True)<now.normalize().tz_convert('UTC')]
    elif fault=='few_days':d=d.tail(20)
    else:d.attrs['source']='UNKNOWN'
    result=predict(d,'BBCA',now)
    assert result['reason'] and all(r['p'] is None for r in result['rows'])


def test_delayed_unfinished_bar_is_not_a_completed_prefix():
    d=fixture();day=pd.Timestamp('2026-10-27T00:00:00+07:00');now=day+pd.Timedelta(hours=9,minutes=40)
    d=d[pd.to_datetime(d.ts,utc=True)<=day.tz_convert('UTC')+pd.Timedelta(hours=9,minutes=0)]
    r=predict(d,'BBCA',now)
    assert pd.Timestamp(r['feature_asof'])==day+pd.Timedelta(hours=9)
    changed=d.copy();mask=pd.to_datetime(changed.ts,utc=True)==day.tz_convert('UTC')+pd.Timedelta(hours=9)
    changed.loc[mask,'close']*=1.1;changed.loc[mask,'high']*=1.2
    assert predict(changed,'BBCA',now)['input_hash']==r['input_hash']


def test_logs_survive_reopen_preserve_issue_time_and_do_not_relabel_dates(tmp_path):
    path=tmp_path/'market.db';now=pd.Timestamp('2026-10-27T08:50:00+07:00')
    r=predict(fixture(),'BBCA',now);record(path,r)
    again=copy.deepcopy(r);again['generated_at']=(now+pd.Timedelta(minutes=5)).isoformat();record(path,again)
    rows=saved(path,'BBCA','2026-10-27')
    assert len(rows)==10 and {x['generated_at'] for x in rows}=={now.isoformat()}
    assert saved(path,'MPXL','2026-10-27')==[] and saved(path,'BBCA','2026-10-28')==[]


def test_no_late_forecast_and_no_scoring_until_provider_confirms_interval_end(tmp_path):
    path=tmp_path/'market.db';d=fixture();issued=pd.Timestamp('2026-10-27T08:50:00+07:00')
    r=predict(d,'BBCA',issued);record(path,r)
    late=copy.deepcopy(r);late['generated_at']='2026-10-27T10:00:00+07:00'
    with pytest.raises(ValueError):record(path,late)
    now=pd.Timestamp('2026-10-27T09:40:00+07:00')
    delayed=d[pd.to_datetime(d.ts,utc=True)<=pd.Timestamp('2026-10-27T09:00:00+07:00').tz_convert('UTC')]
    evaluate(path,'BBCA',delayed,now)
    assert prospective_summary(saved(path,'BBCA','2026-10-27'))['n']==0
    evaluate(path,'BBCA',d,now)
    result=prospective_summary(saved(path,'BBCA','2026-10-27'))
    assert result['n']==1 and result['accuracy_pct'] is not None
    before=saved(path,'BBCA','2026-10-27')[0]['return_pct']
    altered=d.copy();altered['close']*=1.01;altered['high']*=1.02
    evaluate(path,'BBCA',altered,now)
    assert saved(path,'BBCA','2026-10-27')[0]['return_pct']==before


def test_latest_update_cannot_replace_first_forecast_for_accuracy(tmp_path):
    path=tmp_path/'market.db';d=fixture();now=pd.Timestamp('2026-10-27T08:50:00+07:00')
    a=predict(d,'BBCA',now);record(path,a)
    b=copy.deepcopy(a);b['generated_at']='2026-10-27T08:55:00+07:00';b['input_hash']='other'
    for row in b['rows']:
        if row['p'] is not None:row['p']=[1.,0.,0.]
    record(path,b);evaluate(path,'BBCA',d,pd.Timestamp('2026-10-27T10:01:00+07:00'))
    rows=saved(path,'BBCA','2026-10-27');summary=prospective_summary(rows)
    assert summary['recorded']==10 and summary['n']==2
    assert len(rows)==20
    assert table(a,rows,now).shape[0]==15
    assert sum(map(float,percentages([1/3]*3)))==pytest.approx(100)


@pytest.mark.parametrize('p',[[1,1,1],[float('nan'),0,1],[-.1,.5,.6]])
def test_invalid_probabilities_are_not_logged(tmp_path,p):
    r=predict(fixture(),'BBCA',pd.Timestamp('2026-10-27T08:50:00+07:00'))
    next(x for x in r['rows'] if x['p'] is not None)['p']=p
    with pytest.raises(ValueError):record(tmp_path/'market.db',r)


def test_shadow_is_never_promoted_or_upgraded_by_a_checkbox():
    from timemap_forecast_ui import promotion_gate
    assert promotion_gate()['approved'] is False


def test_live_screen_requires_explicit_shadow_display_and_preserves_ledger(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import timemap_forecast_ui as ui
    monkeypatch.setattr(ui,'DB_PATH',tmp_path/'market.db')
    frame=fixture()
    monkeypatch.setattr(ui,'predict',lambda tape,ticker,now,cfg=None:predict(frame,ticker,now,cfg))
    app=AppTest.from_string('''
import pandas as pd
from test_day_forecast import fixture
from market_session import session_status
from timemap_forecast_ui import render_forecast
render_forecast(fixture(),'BBCA',session_status(now=pd.Timestamp('2026-10-27T08:50:00+07:00')))
''').run(timeout=20)
    assert not app.exception
    assert set(app.dataframe[0].value['Prediksi UP % (uji)'])=={'—'}
    app.checkbox[0].check().run(timeout=20)
    assert not app.exception
    assert (app.dataframe[0].value['Prediksi UP % (uji)']!='—').sum()==10
    rows=saved(tmp_path/'market.db','BBCA','2026-10-27')
    assert len(rows)==10
    assert set(r['generated_at'] for r in rows)=={'2026-10-27T08:50:00+07:00'}


def test_forecast_journal_survives_the_existing_sqlite_checkpoint(tmp_path,monkeypatch):
    import gzip
    import persistent_bridge as bridge
    path=tmp_path/'market.db'
    r=predict(fixture(),'BBCA',pd.Timestamp('2026-10-27T08:50:00+07:00'));record(path,r)
    monkeypatch.setattr(bridge,'LOCAL_DB',str(path))
    monkeypatch.setattr(bridge,'DB_DIR',str(tmp_path))
    blob,checksum,size=bridge._consistent_sqlite_bytes()
    assert checksum and size>0
    restored=tmp_path/'restored.db';restored.write_bytes(gzip.decompress(blob))
    assert saved(restored,'BBCA','2026-10-27')==saved(path,'BBCA','2026-10-27')


def test_suspended_or_untraded_today_does_not_generate_numbers():
    d=fixture();now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    d.loc[pd.to_datetime(d.ts,utc=True)>=now.normalize().tz_convert('UTC'),'volume']=0
    result=predict(d,'BBCA',now)
    assert 'VOLUME' in result['reason'] and all(r['p'] is None for r in result['rows'])


def test_second_session_does_not_use_first_session_tape_as_current():
    d=fixture();now=pd.Timestamp('2026-10-27T13:40:00+07:00')
    d=d[pd.to_datetime(d.ts,utc=True)<pd.Timestamp('2026-10-27T12:00:00+07:00').tz_convert('UTC')]
    result=predict(d,'BBCA',now)
    assert 'SESI SAAT INI' in result['reason'] and all(r['p'] is None for r in result['rows'])


def test_cross_year_holiday_unknown_calendar_cannot_reuse_old_forecasts():
    d=fixture()
    for clock in ['2026-10-31T10:00:00+07:00','2026-12-24T10:00:00+07:00','2028-01-04T10:00:00+07:00']:
        result=predict(d,'BBCA',pd.Timestamp(clock))
        assert result['reason'] and all(r['p'] is None for r in result['rows'])


def test_preopen_worker_records_recently_viewed_plan_without_open_app_or_network(tmp_path):
    from timemap_forecast_ui import connect,prepare_watched
    path=tmp_path/'market.db';con=connect(path)
    d=fixture();now=pd.Timestamp('2026-10-27T08:45:00+07:00')
    d=d[pd.to_datetime(d.ts,utc=True)<now.tz_convert('UTC')].copy()
    d['ticker']='BBCA';d['source']='TEST FIXTURE';d.ts=d.ts.astype(str)
    d.to_sql('intraday',con,index=False,if_exists='replace')
    con.execute('INSERT INTO timemap_forecast_watch VALUES(?,?)',('BBCA','2026-10-26T15:00:00+07:00'))
    con.execute('INSERT INTO timemap_forecast_watch VALUES(?,?)',('MPXL','2026-10-01T15:00:00+07:00'))
    con.commit();con.close()
    assert prepare_watched(path,now)=={'checked':1,'recorded':10}
    rows=saved(path,'BBCA','2026-10-27')
    assert len(rows)==10 and all(pd.Timestamp(r['generated_at'])<pd.Timestamp(r['slot']) for r in rows)
    assert prepare_watched(path,now+pd.Timedelta(minutes=5))=={'checked':0,'recorded':0}
    assert {r['generated_at'] for r in saved(path,'BBCA','2026-10-27')}=={now.isoformat()}
    assert prepare_watched(path,pd.Timestamp('2026-10-27T09:30:00+07:00'))=={'checked':0,'recorded':0}


def test_frozen_research_report_matches_model_and_preserves_chronology():
    from pathlib import Path
    from timemap_forecast import MODEL_SHA256,MODEL_ID
    root=Path(__file__).resolve().parents[1]/'validation'/'day_forecast'
    report=json.loads((root/'results.json').read_text())
    assert report['model_sha256']==MODEL_SHA256 and report['model_version']==MODEL_ID
    assert report['forecast_instances']==1008 and report['unique_outcomes']==456
    assert report['promotion'].startswith('SHADOW ONLY')
    for row in json.loads((root/'replay.json').read_text()):
        issue=pd.Timestamp(row['generated_at'])
        assert pd.Timestamp(row['feature_asof'])<=issue
        assert pd.Timestamp(row['training_end']).date()<issue.date()
        assert issue<=pd.Timestamp(row['day']+' '+row['slot'],tz='Asia/Jakarta')
        assert sum(row['p'])==pytest.approx(1) and len(row['p'])==3
