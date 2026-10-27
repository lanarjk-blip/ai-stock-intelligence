"""Display freshness must describe provider evidence, not completed-slot eligibility."""
import pandas as pd
import pytest
import timemap_checkpoint_today as cp

class Base:
    def __init__(self, now, tape):
        self.now=pd.Timestamp(now); self.tape=tape
    def _session(self):
        import market_session
        s=market_session.session_status(now=self.now)
        return s['status'],s['live'],self.now.date().isoformat(),self.now.isoformat()
    def _cfg(self): return {}
    def _provider_frame(self,ticker): return self.tape

def tape(stamps,ticker='ZZZZ',fallback=False):
    d=pd.DataFrame({'ts':pd.to_datetime(stamps,utc=True),'open':100.,'high':102.,'low':99.,'close':101.,'volume':100.})
    d.attrs={'ticker':ticker,'source':'LAST GOOD CACHE' if fallback else 'TEST PROVIDER','fallback':fallback}
    return d

@pytest.mark.parametrize('ticker',['MPXL','BBCA','ANTM','ZZZZ'])
def test_completed_checkpoint_does_not_make_delayed_provider_fresh(ticker):
    d=tape(['2026-10-27T10:00:00+07:00','2026-10-27T10:42:00+07:00'],ticker)
    state=cp._prepare_day(Base('2026-10-27T10:53:00+07:00',d),ticker)
    row=cp._blank_today_row({},ticker,state)
    assert state['fresh'] # internal completed-window eligibility is preserved
    assert row['Kesegaran']=='DELAYED'
    assert pd.Timestamp(row['Candle Provider WIB'])==pd.Timestamp('2026-10-27T10:42:00+07:00')
    assert pd.Timestamp(row['Data Terakhir WIB'])==pd.Timestamp('2026-10-27T10:00:00+07:00')

@pytest.mark.parametrize('now,stamps,fallback,expected',[
    ('2026-10-27T09:05:00+07:00',['2026-10-27T09:03:00+07:00'],False,'DELAYED'),
    ('2026-10-27T10:53:00+07:00',['2026-10-26T15:00:00+07:00'],False,'WAITING FOR NEW BAR'),
    ('2026-10-27T10:53:00+07:00',['2026-10-27T10:00:00+07:00'],True,'DATA STALE'),
    ('2026-10-27T13:40:00+07:00',['2026-10-27T11:30:00+07:00'],False,'DATA STALE'),
])
def test_freshness_does_not_depend_on_completed_window(now,stamps,fallback,expected):
    state=cp._prepare_day(Base(now,tape(stamps,fallback=fallback)),'ZZZZ')
    assert cp._blank_today_row({},'ZZZZ',state)['Kesegaran']==expected

def test_mismatched_identity_does_not_publish_a_provider_candle():
    state=cp._prepare_day(Base('2026-10-27T10:53:00+07:00',tape(['2026-10-27T10:42:00+07:00'],'OTHER')),'ZZZZ')
    row=cp._blank_today_row({},'ZZZZ',state)
    assert row['Candle Provider WIB'] is None
    assert row['Kesegaran']=='DATA UNAVAILABLE'
    assert not state['fresh']
