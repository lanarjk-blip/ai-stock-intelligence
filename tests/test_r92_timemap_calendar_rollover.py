"""Clock-controlled regression: dates roll forward without manual intervention."""
from types import SimpleNamespace
import pandas as pd
import pytest
import timemap_data as data
import timemap_checkpoint_today as cp

@pytest.mark.parametrize('first,second',[
 ('2026-10-26 10:00','2026-10-27 10:00'),
 ('2026-10-30 10:00','2026-11-02 10:00'),
 ('2026-12-30 10:00','2027-01-04 10:00'),
])
def test_open_on_new_date_fetches_that_dates_data(monkeypatch,first,second):
    data._CACHE.clear(); calls=[]
    def download(ticker,days,timeout,now):
        calls.append(now)
        return pd.DataFrame(dict(ts=[now-pd.Timedelta(hours=1)],open=[100],high=[101],low=[99],close=[100],volume=[1000])), 'TEST PROVIDER', []
    monkeypatch.setattr(data,'_download',download)
    a=data.load('TEST',now=pd.Timestamp(first,tz='Asia/Jakarta'))
    b=data.load('TEST',now=pd.Timestamp(second,tz='Asia/Jakarta'))
    assert len(calls)==2
    assert b.ts.iloc[0].tz_convert('Asia/Jakarta').date()==pd.Timestamp(second).date()
    assert b.ts.iloc[0]>a.ts.iloc[0]
    data._CACHE.clear()  # Process restart must refetch; no stale singleton date.
    data.load('TEST',now=pd.Timestamp(second,tz='Asia/Jakarta'))
    assert len(calls)==3
    data._CACHE.clear()


def test_yesterday_never_fills_today_when_provider_has_no_new_data():
    old=pd.DataFrame(dict(ts=pd.to_datetime(['2026-10-26T02:00Z']),open=[100],high=[101],low=[99],close=[100],volume=[1000]))
    base=SimpleNamespace(_session=lambda:('SESSION_1',True,'2026-10-27','2026-10-27T10:00:00+07:00'),_provider_frame=lambda _:old,_cfg=lambda:{})
    state=cp._prepare_day(base,'TEST')
    assert state['frame'].empty
    assert not state['fresh']
    assert state['reason']=='NO BAR FOR ACTIVE MARKET DAY'
