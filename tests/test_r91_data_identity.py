import pandas as pd
import pytest
import timemap_data as data


def test_metadata_symbol_mismatch_rejected():
    frame=pd.DataFrame({'ts':['2026-10-23T02:00Z'],'open':[100],'high':[101],'low':[99],'close':[100],'volume':[10]})
    frame.attrs['ticker']='BBCA'
    with pytest.raises(ValueError,match='TICKER MISMATCH'):
        data.clean(frame,'MPXL',pd.Timestamp('2026-10-23T03:00Z'))


def test_empty_cache_never_claims_last_good_data(monkeypatch):
    data._CACHE.clear()
    monkeypatch.setattr(data,'TTL_SECONDS',0)
    monkeypatch.setattr(data,'_download',lambda *a:(pd.DataFrame(columns=data.COLUMNS),'UNAVAILABLE',['timeout']))
    for _ in range(2):
        frame=data.load('TEST',days=5,now=pd.Timestamp('2026-10-23T03:00Z'))
        assert frame.empty
        assert frame.attrs['source']=='UNAVAILABLE'
    data._CACHE.clear()
