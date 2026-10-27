import sqlite3
import pandas as pd
import pytest
from test_day_forecast import fixture
from timemap_forecast import predict
from timemap_forecast_ui import connect,record,saved,settle_pending,prospective_summary


def store(path,ticker='BBCA',cache_first=False):
    d=fixture(ticker)
    record(path,predict(d,ticker,pd.Timestamp('2026-10-27T08:50:00+07:00')))
    rows=d.copy();rows['ticker']=ticker;rows['source']='TEST FIXTURE';rows.ts=rows.ts.astype(str)
    if cache_first:
        rows.loc[rows.ts.eq('2026-10-27 09:00:00+07:00'),'source']='LAST GOOD CACHE'
    con=sqlite3.connect(path);rows.to_sql('intraday',con,if_exists='append',index=False);con.close()


def test_closed_app_is_not_required_for_pending_outcomes_to_settle(tmp_path):
    path=tmp_path/'market.db';store(path)
    assert prospective_summary(saved(path,'BBCA','2026-10-27'))['n']==0
    result=settle_pending(path,pd.Timestamp('2026-10-27T10:01:00+07:00'))
    assert result=={'checked':1,'new_outcomes':2}
    rows=saved(path,'BBCA','2026-10-27')
    assert prospective_summary(rows)['n']==2
    assert {r['generated_at'] for r in rows}=={'2026-10-27T08:50:00+07:00'}
    assert settle_pending(path,pd.Timestamp('2026-10-27T10:01:00+07:00'))['new_outcomes']==0


def test_cached_target_bar_is_not_certified_by_a_newer_quote(tmp_path):
    path=tmp_path/'market.db';store(path,cache_first=True)
    result=settle_pending(path,pd.Timestamp('2026-10-27T09:40:00+07:00'))
    assert result['new_outcomes']==0
    assert prospective_summary(saved(path,'BBCA','2026-10-27'))['n']==0


def test_bounded_cursor_does_not_starve_valid_tickers_behind_missing_data(tmp_path):
    path=tmp_path/'market.db'
    d=fixture('AAA');record(path,predict(d,'AAA',pd.Timestamp('2026-10-27T08:50:00+07:00')))
    store(path,'BBB')
    now=pd.Timestamp('2026-10-27T10:01:00+07:00')
    assert settle_pending(path,now,limit=1)=={'checked':1,'new_outcomes':0}
    assert settle_pending(path,now,limit=1)=={'checked':1,'new_outcomes':2}
    assert saved(path,'AAA','2026-10-27')[0]['actual_label'] is None
    assert prospective_summary(saved(path,'BBB','2026-10-27'))['n']==2
