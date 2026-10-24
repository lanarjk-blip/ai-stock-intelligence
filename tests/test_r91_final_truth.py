from types import SimpleNamespace
from datetime import datetime
import pandas as pd
import pytest
import market_session as market
import timemap_data as data
import timemap_checkpoint_today as cp
from idx_calendar_sync import parse_calendar

def bars(stamp='2026-10-26T02:00Z'):
    return pd.DataFrame(dict(ts=pd.to_datetime([stamp]),open=[100],high=[101],low=[99],close=[100],volume=[100]))

def test_failed_provider_never_fresh_checkpoint():
    frame=bars(); frame.attrs['fallback']=True
    base=SimpleNamespace(_session=lambda:('SESSION_1',True,'2026-10-26','2026-10-26T09:45:00+07:00'),_provider_frame=lambda _:frame,_cfg=lambda:{})
    state=cp._prepare_day(base,'MPXL')
    assert not state['fresh'] and not state['frame'].empty
    assert state['reason']=='DATA STALE • PROVIDER FAILED'

@pytest.mark.parametrize('when,expected',[
 ('2026-10-26T05:00Z','HISTORICAL ONLY'),
 ('2026-10-26T04:00Z','DATA STALE'),
 ('2026-12-25T03:00Z','HISTORICAL ONLY'),
 ('2028-01-04T03:00Z','CALENDAR UNVERIFIED')])
def test_nonlive_states(when,expected):
    assert data.bar_freshness(bars(),pd.Timestamp(when))==expected

def test_verified_2027_and_unknown_year_fail_closed():
    assert market.session_status(now=datetime(2027,1,5,10))['status']=='HOLIDAY'
    assert market.session_status(now=datetime(2027,1,4,10))['live']
    assert not market.session_status(now=datetime(2028,1,4,10))['live']

def test_same_candle_is_not_new_information(monkeypatch):
    data._CACHE.clear(); monkeypatch.setattr(data,'TTL_SECONDS',0)
    frames=[bars(),bars(),bars('2026-10-26T02:30Z')]
    monkeypatch.setattr(data,'_download',lambda *a:(frames.pop(0),'TEST',[]))
    now=pd.Timestamp('2026-10-26T03:00Z')
    first=data.load('MPXL',now=now); second=data.load('MPXL',now=now); third=data.load('MPXL',now=now)
    assert first.attrs['new_candle'] and not second.attrs['new_candle'] and third.attrs['new_candle']
    data._CACHE.clear()

def test_calendar_parser_rejects_partial_or_unrelated_document():
    with pytest.raises(ValueError): parse_calendar('Kalender Libur Bursa Tahun 2027 1-Jan-2027',2027)
    with pytest.raises(ValueError): parse_calendar('Bursa Efek Indonesia Kalender Libur Bursa Tahun 2027 Lampiran Pengumuman 1 Jumat 1-Jan-2027',2027)

def test_official_2027_attachment_matches_bundled_dates():
    from pathlib import Path
    import json
    parsed=parse_calendar(Path('tests/fixtures/ksei_2027_calendar.txt').read_text(),2027)
    assert parsed==json.loads(Path('data/idx_holidays.json').read_text())['holidays']['2027']

def test_session_change_forces_fetch(monkeypatch):
    data._CACHE.clear(); calls=[]
    def fetch(*args):
        calls.append(args); return bars(),'TEST',[]
    monkeypatch.setattr(data,'_download',fetch)
    data.load('MPXL',now=pd.Timestamp('2026-10-26T04:59Z'))
    data.load('MPXL',now=pd.Timestamp('2026-10-26T05:00Z'))
    assert len(calls)==2
    data._CACHE.clear()

def test_all_configured_universe_tickers_keep_identity(monkeypatch):
    import json
    from pathlib import Path
    symbols=json.loads(Path('tests/fixtures/idx_symbols.json').read_text())
    data._CACHE.clear()
    def fetch(ticker,*args):
        f=bars(); f['volume']=symbols.index(ticker)+1
        return f,'TEST',[]
    monkeypatch.setattr(data,'_download',fetch)
    for symbol in symbols:
        frame=data.load(symbol+'.JK',now=pd.Timestamp('2026-10-26T03:00Z'))
        assert frame.attrs['ticker']==symbol
        assert frame.volume.iloc[0]==symbols.index(symbol)+1
    assert len(data._CACHE)<=data.MAX_CACHE
    data._CACHE.clear()
