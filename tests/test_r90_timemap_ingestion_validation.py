from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
import pandas as pd
import pytest

import timemap_data as data
import timemap_statistics as stats
import timemap_checkpoint_today as cp

NOW = pd.Timestamp('2026-10-23 14:15', tz='Asia/Jakarta')


def bars(ts=('2026-10-23 02:00Z',), price=100.):
    return pd.DataFrame(dict(ts=pd.to_datetime(list(ts),utc=True),open=price,high=price+2,low=price-2,close=price+1,volume=1000.))


@pytest.fixture(autouse=True)
def empty_cache():
    data._CACHE.clear()
    yield
    data._CACHE.clear()


def test_bad_prices_future_and_duplicate_rows_are_rejected():
    frame=bars(['2026-10-23 02:00Z']*2+['2026-10-23 02:30Z','2026-10-23 08:00Z'])
    frame.loc[2,'high']=50
    assert len(data.clean(frame,'TEST',NOW))==1
    frame['ticker']='WRONG'
    with pytest.raises(ValueError,match='TICKER MISMATCH'):
        data.clean(frame,'TEST',NOW)


def test_empty_first_host_reaches_second_host_and_checks_symbol(monkeypatch):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        payload={'chart':{'result':[]}} if len(calls)==1 else {'chart':{'result':[{'meta':{'symbol':'TEST.JK'},'timestamp':[1792720800],'indicators':{'quote':[dict(open=[100],high=[102],low=[98],close=[101],volume=[1000])]}}]}}
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:payload)
    monkeypatch.setattr(data.requests,'get',get)
    out=data.load('test.jk',now=NOW)
    assert len(out)==1 and len(calls)==2
    assert out.attrs['source']=='query2.finance.yahoo.com'
    assert out.attrs['errors']
    data._CACHE.clear()
    wrong=data.load('OTHER',now=NOW)
    assert wrong.empty and wrong.attrs['source']=='UNAVAILABLE'


def test_cache_is_scoped_by_symbol_date_range_and_returns_copies(monkeypatch):
    calls=[]
    def download(ticker,*args):
        calls.append(ticker)
        return bars(price=100 if ticker=='AAAA' else 200),'fixture',[]
    monkeypatch.setattr(data,'_download',download)
    first=data.load('AAAA',now=NOW); first.loc[0,'close']=999
    second=data.load('AAAA',now=NOW)
    assert second.attrs['cache_hit'] and second.close.iloc[0]==101
    assert data.load('BBBB',now=NOW).close.iloc[0]==201
    data.load('AAAA',now=NOW+pd.Timedelta(days=1))
    data.load('AAAA',days=5,now=NOW)
    assert len(calls)==4


def test_expired_refresh_failure_retains_only_dated_data(monkeypatch):
    monkeypatch.setattr(data,'TTL_SECONDS',0)
    monkeypatch.setattr(data,'_download',lambda *a:(bars(),'fixture',[]))
    first=data.load('TEST',now=NOW)
    monkeypatch.setattr(data,'_download',lambda *a:(pd.DataFrame(columns=data.COLUMNS),'UNAVAILABLE',['HTTP 429']))
    failed=data.load('TEST',now=NOW+pd.Timedelta(minutes=2))
    assert failed.ts.equals(first.ts)
    assert failed.attrs['source']=='LAST GOOD CACHE'
    assert failed.attrs['errors']==['HTTP 429']


def test_concurrent_same_key_downloads_once(monkeypatch):
    calls=[]
    def download(*args):
        calls.append(1);time.sleep(.02)
        return bars(),'fixture',[]
    monkeypatch.setattr(data,'_download',download)
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames=list(pool.map(lambda _:data.load('TEST',now=NOW),range(4)))
    assert len(calls)==1 and all(len(x)==1 for x in frames)


def test_historical_validation_never_uses_current_or_future_outcomes():
    ts=pd.bdate_range('2026-09-01', '2026-10-23',tz='Asia/Jakarta')+pd.Timedelta(hours=9)
    frame=bars(ts);frame['close']=np.where(np.arange(len(frame))%2,101,99)
    obs=stats.observations(frame,'TEST',NOW)
    assert max(obs.day)<NOW.date()
    summary,ev=stats.walk_forward(obs)
    assert summary['n']>0 and (ev.train_end<ev.day).all()
    changed=frame.copy();changed.loc[changed.ts.dt.date>=pd.Timestamp('2026-10-15').date(),'close']=102
    _,ev2=stats.walk_forward(stats.observations(changed,'TEST',NOW))
    pd.testing.assert_frame_equal(ev[ev.day<'2026-10-15'],ev2[ev2.day<'2026-10-15'])
    baseline=stats.build('TEST',frame,NOW)
    assert baseline.confidence_pct.isna().all()
    assert baseline.set_index('time').loc['13:30','session']=='BREAK'


def base_for(frame,now):
    import market_session
    session=market_session.session_status({},now=now)
    return SimpleNamespace(_session=lambda:(session['status'],session['live'],session['market_date'],session['wib']),_provider_frame=lambda _:frame,_cfg=lambda:{})


def test_forming_candle_is_not_realized_and_missing_slot_not_backfilled():
    frame=bars(['2026-10-23 02:00Z','2026-10-23 07:00Z'])
    state=cp._prepare_day(base_for(frame,NOW),'TEST')
    assert list(state['frame'].minute)==[540]
    assert cp._checkpoint_metrics(state['frame'],570) is None
    completed=cp._prepare_day(base_for(frame,NOW+pd.Timedelta(minutes=15)),'TEST')
    assert list(completed['frame'].minute)==[540,840]
    assert cp._checkpoint_metrics(completed['frame'],840)['available_at'].startswith('2026-10-23T14:30')


def test_final_30m_bar_is_not_released_at_regular_session_close():
    frame=bars(['2026-10-23 08:30Z'])
    assert cp._prepare_day(base_for(frame,pd.Timestamp('2026-10-23 15:55',tz='Asia/Jakarta')),'TEST')['frame'].empty
    assert len(cp._prepare_day(base_for(frame,pd.Timestamp('2026-10-23 16:00',tz='Asia/Jakarta')),'TEST')['frame'])==1


def test_explicit_table_ticker_wins_over_other_widget(monkeypatch):
    seen=[]
    state=dict(market_date='2026-10-23',wib=NOW.isoformat(),frame=pd.DataFrame(),fresh=False,market_status='SESSION_2',reason='NO DATA',source='fixture')
    monkeypatch.setattr(cp,'_prepare_day',lambda base,ticker:seen.append(ticker) or state)
    frame=pd.DataFrame([{'Waktu':'14:00','Sesi':'Sesi II'}]);frame.attrs['ticker']='AAAA'
    base=SimpleNamespace(_active_ticker=lambda _: 'BBBB',_time_column=lambda _: 'Waktu')
    out=cp._checkpoint_rows(frame,None,base)
    assert seen==['AAAA'] and out['Live Ticker'].iloc[0]=='AAAA'
    assert out['Match %'].isna().all()


def test_weekend_preserves_historical_baseline_for_next_session():
    frame=bars(pd.bdate_range('2026-09-01','2026-10-23',tz='UTC')+pd.Timedelta(hours=2))
    out=stats.build('TEST',frame,pd.Timestamp('2026-10-24 00:15',tz='Asia/Jakarta'))
    assert out.up_pct.notna().any()
    assert set(out.schedule_date)=={'2026-10-26'}
    assert set(out.historical_asof)=={'2026-10-23'}
    state=dict(market_date='2026-10-24',wib='2026-10-24T00:15:00+07:00',frame=pd.DataFrame(),fresh=False,market_status='WEEKEND',reason='NO DATA',source='fixture')
    # A Saturday schedule never grants live trust to Monday's historical baseline.
    assert not out.trusted.any()
