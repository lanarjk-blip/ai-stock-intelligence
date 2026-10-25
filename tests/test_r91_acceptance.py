from types import SimpleNamespace
import pandas as pd
import pytest
import timemap_data as data
import timemap_checkpoint_today as cp
import market_session as market


def candle(stamp):
 return pd.DataFrame(dict(ts=[stamp.tz_convert('UTC')],open=[100],high=[102],low=[99],close=[101],volume=[1000]))

def state(frame,now):
 ss=market.session_status(now=now)
 base=SimpleNamespace(_session=lambda:(ss['status'],ss['live'],ss['market_date'],ss['wib']),_provider_frame=lambda _:frame,_cfg=lambda:{})
 return cp._prepare_day(base,'MPXL')

@pytest.mark.parametrize('before,after',[
 ('2026-10-23 15:00','2026-10-26 10:00'),
 ('2026-10-26 15:00','2026-10-27 10:00'),
 ('2026-10-30 15:00','2026-11-02 10:00'),
 ('2026-12-30 15:00','2027-01-04 10:00'),
 ('2026-12-23 15:00','2026-12-28 10:00'),
])
def test_rollover_rejects_old_candle_and_accepts_new(monkeypatch,before,after):
 data._CACHE.clear()
 old=pd.Timestamp(before,tz='Asia/Jakarta'); new=pd.Timestamp(after,tz='Asia/Jakarta')
 frames=[candle(old),candle(old),candle(new-pd.Timedelta(minutes=30))]
 monkeypatch.setattr(data,'TTL_SECONDS',0)
 monkeypatch.setattr(data,'_download',lambda *a:(frames.pop(0),'TEST',[]))
 original=data.load('MPXL',now=old+pd.Timedelta(minutes=30))
 stale=data.load('MPXL',now=new)
 assert stale.ts.iloc[0]==original.ts.iloc[0]
 assert not state(stale,new)['fresh'] and state(stale,new)['frame'].empty
 fresh=data.load('MPXL',now=new)
 observed=state(fresh,new)
 assert observed['fresh'] and len(observed['frame'])==1
 assert observed['frame'].ts.iloc[0].tz_convert('Asia/Jakarta').date()==new.date()
 data._CACHE.clear()

@pytest.mark.parametrize('clock,stamp,expected',[
 ('08:00','2026-10-23 15:00','WAITING FOR NEW BAR'),
 ('09:00','2026-10-23 15:00','WAITING FOR NEW BAR'),
 ('10:00','2026-10-26 09:30','DELAYED'),
 ('13:30','2026-10-26 11:30','DATA STALE'),
 ('14:00','2026-10-26 13:30','DELAYED'),
 ('16:30','2026-10-26 15:00','HISTORICAL ONLY'),
])
def test_open_and_reopen_checks_market_evidence(monkeypatch,clock,stamp,expected):
 data._CACHE.clear();calls=[]
 now=pd.Timestamp('2026-10-26 '+clock,tz='Asia/Jakarta')
 def fetch(*args):
  calls.append(args);return candle(pd.Timestamp(stamp,tz='Asia/Jakarta')),'TEST',[]
 monkeypatch.setattr(data,'_download',fetch)
 first=data.load('MPXL',now=now)
 assert first.attrs['data_status']==expected
 data._CACHE.clear() # Fresh server session/process; physical WebView tested separately.
 second=data.load('MPXL',now=now)
 assert len(calls)==2 and second.ts.equals(first.ts)
 assert not second.attrs.get('forecast_generated_at')
 data._CACHE.clear()

def test_session_two_never_uses_session_one_as_fresh(monkeypatch):
 data._CACHE.clear(); calls=[]
 def fetch(*args):
  calls.append(args);return candle(pd.Timestamp('2026-10-26 11:00',tz='Asia/Jakarta')),'TEST',[]
 monkeypatch.setattr(data,'_download',fetch)
 data.load('MPXL',now=pd.Timestamp('2026-10-26 11:59',tz='Asia/Jakarta'))
 now=pd.Timestamp('2026-10-26 13:30',tz='Asia/Jakarta')
 second=data.load('MPXL',now=now)
 assert len(calls)==2 and second.attrs['data_status']=='DATA STALE'
 assert not state(second,now)['fresh']
 data._CACHE.clear()
