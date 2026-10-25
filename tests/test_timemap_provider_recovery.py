from types import SimpleNamespace
import pandas as pd
import pytest
import timemap_data as data

NOW=pd.Timestamp('2026-10-26T03:00Z')

def bars(stamp):
    return pd.DataFrame(dict(ts=pd.to_datetime([stamp]),open=[100],high=[101],low=[99],close=[100],volume=[10]))

@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    data._CACHE.clear();monkeypatch.setattr(data,'TTL_SECONDS',0)
    yield
    data._CACHE.clear()

def test_long_history_failure_still_recovers_today_when_cache_exists(monkeypatch):
    calls=[]
    def fetch(ticker,days,*args):
        calls.append(days)
        if len(calls)==1:return bars('2026-10-26T02:00Z'),'primary',[]
        if days==59:return pd.DataFrame(columns=data.COLUMNS),'UNAVAILABLE',['timeout']
        return bars('2026-10-26T02:30Z'),'secondary',[]
    monkeypatch.setattr(data,'_download',fetch)
    first=data.load('MPXL',now=NOW)
    recovered=data.load('MPXL',now=NOW+pd.Timedelta(minutes=1))
    assert calls==[59,59,5]
    assert recovered.ts.max()>first.ts.max() and recovered.attrs['new_candle']
    assert not recovered.attrs['fallback'] and recovered.attrs['data_status']=='DELAYED'

def test_older_successful_response_never_replaces_newer_cache(monkeypatch):
    frames=[bars('2026-10-26T02:30Z'),bars('2026-10-26T02:00Z')]
    monkeypatch.setattr(data,'_download',lambda *a:(frames.pop(0),'primary',[]))
    first=data.load('MPXL',days=5,now=NOW)
    second=data.load('MPXL',days=5,now=NOW+pd.Timedelta(minutes=1))
    assert second.ts.equals(first.ts)
    assert second.attrs['fetched_at']==first.attrs['fetched_at']
    assert second.attrs['fallback'] and second.attrs['data_status']=='DATA STALE'
    assert not second.attrs['new_candle']
    assert 'REGRESSION' in second.attrs['errors'][0]

def test_first_seen_friday_history_on_monday_is_not_new_market_information(monkeypatch):
    monkeypatch.setattr(data,'_download',lambda *a:(bars('2026-10-23T08:00Z'),'primary',[]))
    result=data.load('MPXL',now=NOW)
    assert result.attrs['data_status']=='WAITING FOR NEW BAR'
    assert not result.attrs['new_candle']

def test_successful_stale_primary_tries_secondary_and_uses_newer_bar(monkeypatch):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        stamp='2026-10-23T08:00Z' if len(calls)==1 else '2026-10-26T02:30Z'
        payload={'chart':{'result':[{'meta':{'symbol':'MPXL.JK'},'timestamp':[int(pd.Timestamp(stamp).timestamp())],
                 'indicators':{'quote':[dict(open=[100],high=[101],low=[99],close=[100],volume=[10])]}}]}}
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:payload)
    monkeypatch.setattr(data.requests,'get',get)
    frame=data.load('MPXL',days=5,now=NOW)
    assert len(calls)==2 and frame.attrs['source']=='query2.finance.yahoo.com'
    assert frame.ts.max()==pd.Timestamp('2026-10-26T02:30Z') and frame.attrs['new_candle']

def test_rate_limit_does_not_trigger_extra_history_retry(monkeypatch):
    calls=[]
    def fetch(*args):
        calls.append(args);return pd.DataFrame(columns=data.COLUMNS),'UNAVAILABLE',['HTTP 429']
    monkeypatch.setattr(data,'_download',fetch)
    frame=data.load('MPXL',now=NOW)
    assert frame.empty and len(calls)==1 and not frame.attrs['new_candle']
