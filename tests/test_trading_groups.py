import sqlite3
import pandas as pd
import pytest
from market_session import session_status
from trading_groups import split_groups,latest_candles,render_groups,group_summary


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


@pytest.mark.parametrize('fallback',[False,True])
def test_persistence_keeps_original_fetch_time_and_marks_fallback(fallback):
    from timemap_data import storage_provenance
    frame=pd.DataFrame()
    frame.attrs=dict(source='query1.finance.yahoo.com',fallback=fallback,fetched_at='2026-10-26T03:00:00Z')
    original=dict(frame.attrs)
    source,stamp=storage_provenance(frame,pd.Timestamp('2026-10-27T03:00:00Z'))
    assert stamp=='2026-10-26T03:00:00+00:00'
    assert source==('LAST GOOD CACHE' if fallback else 'query1.finance.yahoo.com')
    assert frame.attrs==original


def test_missing_fallback_fetch_time_cannot_be_replaced_by_refresh_time():
    from timemap_data import storage_provenance
    frame=pd.DataFrame();frame.attrs={'fallback':True}
    assert storage_provenance(frame,pd.Timestamp('2026-10-27T03:00:00Z'))==('LAST GOOD CACHE',None)


def test_two_side_by_side_lists_and_literal_ticker_filter_render():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
from test_trading_groups import setup
from trading_groups import render_groups
render_groups(*setup(),key='group_test')
''').run(timeout=20)
    assert not app.exception
    markup=' '.join(m.value for m in app.markdown)
    assert 'Lolos saringan · 1' in markup and 'Tidak lolos / ⚪ Belum dinilai · 2' in markup
    assert '?ticker=BBCA' in markup and '27-10-2026 10:00' in markup
    app.text_input[0].set_value('ANTM').run(timeout=20)
    markup=' '.join(m.value for m in app.markdown)
    assert 'Lolos saringan · 0' in markup and 'Tidak lolos / ⚪ Belum dinilai · 1' in markup
    assert '?ticker=BBCA' not in markup and '?ticker=ANTM' in markup


def test_three_groups_are_exclusive_and_percentages_sum_to_100():
    from decimal import Decimal
    universe,dynamic,candles,session,quality=setup()
    bad=dynamic.iloc[0].copy();bad['ticker']='ANTM';bad['risk']='HIGH'
    bar=candles.iloc[0].copy();bar['ticker']='ANTM'
    accepted,waiting=split_groups(universe,pd.concat([dynamic,bad.to_frame().T]),
                                  pd.concat([candles,bar.to_frame().T]),session,quality)
    assert accepted.Ticker.tolist()==['BBCA']
    assert waiting.set_index('Ticker').Kelompok.to_dict()=={'ANTM':'Tidak lolos','NEW':'Belum bisa dinilai'}
    summary=group_summary(accepted,waiting)
    assert summary.Jumlah.tolist()==[1,1,1]
    assert sum(Decimal(v[:-1]) for v in summary.Persentase)==Decimal('100.00')


@pytest.mark.parametrize('field,value',[('ts','2026-10-26T10:00:00+07:00'),('source','LAST GOOD CACHE'),('volume',None)])
def test_data_failure_takes_precedence_over_setup_rejection(field,value):
    u,d,c,s,q=setup();d.loc[0,'risk']='HIGH';c.loc[0,field]=value
    a,w=split_groups(u,d,c,s,q)
    assert a.empty and set(w.Kelompok)=={'Belum bisa dinilai'}


def test_missing_risk_values_are_not_assigned_a_rejection_or_accuracy():
    u,d,c,s,q=setup();d.loc[0,'stop_loss']=None
    a,w=split_groups(u,d,c,s,q)
    assert a.empty and set(w.Kelompok)=={'Belum bisa dinilai'}
    assert 'Angka entry' in w.set_index('Ticker').loc['BBCA','Alasan']


def test_empty_universe_summary_has_zero_counts_not_fake_100_percent():
    u,d,c,s,q=setup();a,w=split_groups(u.iloc[:0],d,c,s,q)
    summary=group_summary(a,w)
    assert summary.Jumlah.tolist()==[0,0,0]
    assert summary.Persentase.tolist()==['0.00%']*3


def test_closed_market_is_unassessed_not_rejected():
    u,d,c,s,q=setup();s=session_status(now=pd.Timestamp('2026-10-27T08:00:00+07:00'))
    a,w=split_groups(u,d,c,s,q)
    assert a.empty and group_summary(a,w).Jumlah.tolist()==[0,0,3]


def test_every_ticker_is_reachable_and_search_does_not_change_universe_shares():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
import pandas as pd
from test_trading_groups import setup
from trading_groups import render_groups
u,d,c,s,q=setup()
u=pd.DataFrame({'ticker':[f'T{i:03}' for i in range(95)]})
render_groups(u,d,c,s,q,key='pages')
''').run(timeout=20)
    assert not app.exception
    assert app.table[0].value.Jumlah.tolist()==[0,0,95]
    seen=set()
    import re
    for page in [1,2,3]:
        app.selectbox[1].set_value(page).run(timeout=20)
        assert not app.exception
        seen.update(re.findall(r'\?ticker=(T\d{3})',' '.join(m.value for m in app.markdown)))
    assert seen=={f'T{i:03}' for i in range(95)}
    app.text_input[0].set_value('T094').run(timeout=20)
    assert not app.exception
    assert app.table[0].value.Jumlah.tolist()==[0,0,95]
    markup=' '.join(m.value for m in app.markdown)
    assert '?ticker=T094' in markup and '?ticker=T001' not in markup
    assert 'BELUM TERBUKTI' in ' '.join(c.value for c in app.caption)
