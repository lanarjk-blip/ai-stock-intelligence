import sqlite3
import pandas as pd
import pytest
from market_session import session_status
from trading_groups import split_groups,latest_candles,render_groups


def setup():
    session=session_status(now=pd.Timestamp('2026-10-27T10:03:00+07:00'))
    universe=pd.DataFrame({'ticker':['BBCA','ANTM','NEW']})
    dynamic=pd.DataFrame([dict(ticker='BBCA',generated_at='2026-10-27T10:02:00+07:00',candidate_mode='STRICT',
        state='ENTRY READY',entry_status='ACTIVE',decision='BUY',close=100,risk='LOW',market_regime='BULLISH',
        entry_low=99,entry_high=101,stop_loss=98,target_1=104,risk_reward=2.)])
    candles=pd.DataFrame([dict(ticker='BBCA',ts='2026-10-27T10:00:00+07:00',open=100,close=100,volume=1000,source='TEST')])
    return universe,dynamic,candles,session,{'known':True,'ready':True}


def test_all_tickers_are_partitioned_once_and_missing_evidence_is_wait():
    accepted,waiting=split_groups(*setup())
    assert accepted.Ticker.tolist()==['BBCA']
    assert set(waiting.Ticker)=={'ANTM','NEW'}
    assert set(accepted.Ticker).isdisjoint(waiting.Ticker)
    assert len(accepted)+len(waiting)==3


@pytest.mark.parametrize('field,value',[
    ('candidate_mode','MONITOR_FALLBACK'),('entry_status','DEVELOPING'),('state','MONITOR'),
    ('decision','SELL/AVOID'),('risk','HIGH'),('market_regime','BEARISH'),('market_regime','HIGH_VOLATILITY'),
    ('generated_at','2026-10-26T10:02:00+07:00'),('generated_at','2026-10-27T10:04:00+07:00'),
    ('generated_at','2026-10-27 10:02:00'),('close',99),('stop_loss',101),('target_1',101),('risk_reward',1.),
])
def test_blocked_or_changed_dynamic_can_never_become_candidate(field,value):
    universe,dynamic,candles,session,quality=setup();dynamic.loc[0,field]=value
    accepted,waiting=split_groups(universe,dynamic,candles,session,quality)
    assert accepted.empty and len(waiting)==3


@pytest.mark.parametrize('field,value',[
    ('ts','2026-10-26T10:00:00+07:00'),('ts','2026-10-27T09:30:00+07:00'),
    ('ts','2026-10-27T10:04:00+07:00'),('volume',0),('volume',None),
    ('source','LAST GOOD CACHE'),('source',None),('ticker','MPXL'),
    ('open',102),('open',None),
])
def test_stale_empty_cross_ticker_or_fallback_candles_never_qualify(field,value):
    universe,dynamic,candles,session,quality=setup();candles.loc[0,field]=value
    assert split_groups(universe,dynamic,candles,session,quality)[0].empty


def test_session_rollover_and_market_closed_reclassify_automatically():
    universe,dynamic,candles,session,quality=setup()
    for at in ['2026-10-27T08:00:00+07:00','2026-10-27T14:03:00+07:00',
               '2026-10-27T17:00:00+07:00','2026-10-28T10:03:00+07:00']:
        session=session_status(now=pd.Timestamp(at))
        assert split_groups(universe,dynamic,candles,session,quality)[0].empty
    assert split_groups(universe,dynamic,candles,setup()[3],{'known':False,'ready':False})[0].empty
    assert split_groups(universe,dynamic,candles,setup()[3],{'known':True,'ready':False})[0].empty


def test_conflicting_duplicate_snapshots_fail_closed_and_inputs_unchanged():
    universe,dynamic,candles,session,quality=setup()
    duplicated=pd.concat([dynamic,dynamic],ignore_index=True)
    assert split_groups(universe,duplicated,candles,session,quality)[0].empty
    before=dynamic.copy(deep=True)
    split_groups(universe,dynamic,candles,session,quality)
    pd.testing.assert_frame_equal(before,dynamic)


def test_saved_candle_query_uses_actual_timestamp_not_refresh_time(tmp_path):
    path=tmp_path/'data.db';con=sqlite3.connect(path)
    con.execute('CREATE TABLE intraday(ticker TEXT,ts TEXT,open REAL,close REAL,volume REAL,source TEXT)')
    con.executemany('INSERT INTO intraday VALUES(?,?,?,?,?,?)',[
        ('BBCA','2026-10-26T10:00:00+07:00',98,98,100,'TEST'),
        ('BBCA','2026-10-27T03:00:00+00:00',100,100,200,'TEST')])
    con.commit();con.close()
    result=latest_candles(path)
    assert len(result)==1 and result.iloc[0]['close']==100
    assert result.iloc[0]['ts']=='2026-10-27T03:00:00+00:00'


def test_two_side_by_side_lists_and_literal_ticker_filter_render():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
from test_trading_groups import setup
from trading_groups import render_groups
render_groups(*setup(),key='group_test')
''').run(timeout=20)
    assert not app.exception
    markup=' '.join(m.value for m in app.markdown)
    assert 'Kandidat trading · 1' in markup and 'Belum layak / WAIT · 2' in markup
    assert '?ticker=BBCA' in markup and '27-10-2026 10:00' in markup
    app.text_input[0].set_value('ANTM').run(timeout=20)
    markup=' '.join(m.value for m in app.markdown)
    assert 'Kandidat trading · 0' in markup and 'Belum layak / WAIT · 1' in markup
    assert '?ticker=BBCA' not in markup and '?ticker=ANTM' in markup
