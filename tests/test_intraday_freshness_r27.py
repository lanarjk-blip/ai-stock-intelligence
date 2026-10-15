from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


def bars(count=60, *, end=None, price=100.0):
    end=end or datetime(2026,10,15,3,0,tzinfo=timezone.utc)
    ts=pd.date_range(end=end, periods=count, freq='30min', tz='UTC')
    return pd.DataFrame({
        'ts':ts,
        'open':[price]*count,
        'high':[price+1]*count,
        'low':[price-1]*count,
        'close':[price]*count,
        'volume':[1000]*count,
    })


def live_status():
    return {'live':True,'status':'SESSION_1','market_date':'2026-10-15'}


def closed_status():
    return {'live':False,'status':'CLOSED','market_date':'2026-10-15'}


def test_fresh_live_history_does_not_refetch(monkeypatch):
    import app
    now=datetime(2026,10,15,3,10,tzinfo=timezone.utc)
    df=bars(60,end=now-timedelta(minutes=10))
    monkeypatch.setattr(app.market_session,'session_status',lambda cfg,now=None:live_status())
    calls=[]
    monkeypatch.setattr(app,'fetch_chart',lambda *a,**k:calls.append((a,k)))
    out,meta=app._refresh_intraday_if_needed('BBCA',df,now=now)
    assert len(out)==60
    assert meta['mode']=='CACHE'
    assert meta['attempted'] is False
    assert calls==[]


def test_stale_live_history_gets_lightweight_refresh_and_is_throttled(monkeypatch):
    import app
    now=datetime(2026,10,15,3,30,tzinfo=timezone.utc)
    stale=bars(60,end=now-timedelta(minutes=60),price=100)
    fresh=bars(10,end=now-timedelta(minutes=5),price=105)
    reloaded=pd.concat([stale,fresh],ignore_index=True).drop_duplicates('ts').sort_values('ts')
    state={}
    calls=[]
    monkeypatch.setattr(app.market_session,'session_status',lambda cfg,now=None:live_status())
    monkeypatch.setattr(app,'state_get',lambda k:state.get(k))
    monkeypatch.setattr(app,'state_set',lambda k,v:state.__setitem__(k,str(v)))
    monkeypatch.setattr(app,'fetch_chart',lambda ticker,period,interval:(calls.append((ticker,period,interval)) or fresh))
    monkeypatch.setattr(app,'save_intraday',lambda df,ticker:len(df))
    monkeypatch.setattr(app,'_intraday_load',lambda ticker:reloaded)

    out,meta=app._refresh_intraday_if_needed('BBCA',stale,now=now)
    assert meta['mode']=='LIGHT_REFRESH' and meta['updated'] is True
    assert calls==[('BBCA',2,app.CFGD.get('intraday_interval','30m'))]
    assert float(out.iloc[-1].close)==105.0

    calls.clear()
    out2,meta2=app._refresh_intraday_if_needed('BBCA',stale,now=now+timedelta(minutes=10))
    assert meta2['mode']=='THROTTLED'
    assert calls==[]


def test_insufficient_history_fetches_full_history_even_off_market(monkeypatch):
    import app
    now=datetime(2026,10,15,10,0,tzinfo=timezone.utc)
    short=bars(20,end=now-timedelta(hours=1))
    full=bars(80,end=now-timedelta(hours=1))
    state={}
    calls=[]
    monkeypatch.setattr(app.market_session,'session_status',lambda cfg,now=None:closed_status())
    monkeypatch.setattr(app,'state_get',lambda k:state.get(k))
    monkeypatch.setattr(app,'state_set',lambda k,v:state.__setitem__(k,str(v)))
    monkeypatch.setattr(app,'fetch_chart',lambda ticker,period,interval:(calls.append(period) or full))
    monkeypatch.setattr(app,'save_intraday',lambda df,ticker:len(df))
    monkeypatch.setattr(app,'_intraday_load',lambda ticker:full)
    out,meta=app._refresh_intraday_if_needed('TLKM',short,now=now)
    assert meta['mode']=='FULL_HISTORY'
    assert calls==[app.CFGD.get('intraday_period_days',60)]
    assert len(out)==80


def test_sufficient_history_does_not_refetch_after_close(monkeypatch):
    import app
    now=datetime(2026,10,15,10,0,tzinfo=timezone.utc)
    df=bars(80,end=now-timedelta(hours=4))
    monkeypatch.setattr(app.market_session,'session_status',lambda cfg,now=None:closed_status())
    calls=[]
    monkeypatch.setattr(app,'fetch_chart',lambda *a,**k:calls.append((a,k)))
    mode,reason=app._intraday_refresh_plan(df,now=now)
    assert mode=='NONE' and reason=='session_CLOSED'
    assert calls==[]


def test_latest_intraday_quote_requires_same_day_and_fresh_live_bar(monkeypatch):
    import app
    now=datetime(2026,10,15,3,30,tzinfo=timezone.utc)
    monkeypatch.setattr(app.market_session,'session_status',lambda cfg,now=None:live_status())

    old=bars(60,end=datetime(2026,10,14,8,0,tzinfo=timezone.utc),price=90)
    monkeypatch.setattr(app,'_intraday_load',lambda ticker:old)
    assert app.latest_intraday_quote('BBCA',now=now) is None

    stale=bars(60,end=now-timedelta(minutes=90),price=95)
    monkeypatch.setattr(app,'_intraday_load',lambda ticker:stale)
    assert app.latest_intraday_quote('BBCA',now=now) is None

    fresh=bars(60,end=now-timedelta(minutes=20),price=101)
    monkeypatch.setattr(app,'_intraday_load',lambda ticker:fresh)
    q=app.latest_intraday_quote('BBCA',now=now)
    assert q is not None
    assert q['price']==101.0
    assert q['source']=='INTRADAY_TODAY'
    assert q['market_date']=='2026-10-15'


def test_dynamic_time_signal_carries_live_quote_without_fabricating_time_model(monkeypatch):
    import dynamic_engine as d
    monkeypatch.setattr(d.app,'intraday_prediction',lambda ticker:pd.DataFrame())
    monkeypatch.setattr(d.app,'latest_intraday_quote',lambda ticker:{
        'price':1234.0,'ts':'2026-10-15T03:00:00+00:00','source':'INTRADAY_TODAY'
    })
    sig=d._cached_time_signal('BBCA')
    assert sig['current_price']==1234.0
    assert sig['price_source']=='INTRADAY_TODAY'
    assert sig['entry_status']=='NEUTRAL'
    assert sig['edge']==0.0


def test_dynamic_entry_uses_current_intraday_price_only_for_zone_refinement():
    import dynamic_engine as d
    row=pd.Series({'close':100.0,'entry_low':95.0,'entry_high':110.0})
    lo,hi=d._dynamic_entry(row,{
        'current_price':120.0,'edge':0.06,'confidence':0.50,
    })
    assert lo==95.0
    assert hi==110.0  # min(original high, 120*1.005) preserves risk-engine ceiling

    lo2,hi2=d._dynamic_entry(row,{
        'current_price':90.0,'edge':-0.06,'confidence':0.50,
    })
    assert lo2==95.0
    assert hi2==95.0  # refinement cannot invert the original entry zone


def test_source_contract_uses_market_price_but_does_not_rewrite_model_probability():
    app_src=Path('app.py').read_text(encoding='utf-8')
    dyn_src=Path('dynamic_engine.py').read_text(encoding='utf-8')
    assert 'def _intraday_refresh_plan' in app_src
    assert 'def latest_intraday_quote' in app_src
    assert "period=(CFGD.get('intraday_period_days',60) if mode=='FULL_HISTORY' else 2)" in app_src
    assert "'close':market_price" in dyn_src
    assert "live_price=ts.get('current_price')" in dyn_src
    assert "pup=float(row.jst_p_up)*100" in dyn_src
