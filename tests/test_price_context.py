import pandas as pd
import pytest
from price_context import previous_close, change_pct, timemap_sections

@pytest.mark.parametrize('ticker',['PTSP','MPXL','BBCA','ANTM','NEW'])
def test_daily_change_uses_prior_day_not_today_snapshot(ticker):
    prices=pd.DataFrame({'date':['2026-10-23','2026-10-26'],'close':[2250,2810]})
    reference=previous_close(prices,'2026-10-26')
    assert reference==2250
    assert change_pct(2100,reference)==pytest.approx(-6.6666667)
    assert previous_close(prices,'2026-10-23') is None

def test_today_view_cannot_display_historical_up_as_live():
    frame=pd.DataFrame({'Data Type':['TODAY CHECKPOINT','HISTORICAL BASELINE'],'Waktu':['14:00','14:00'],'Slot Return %':[-7,None],'Baseline UP %':[60,None],'UP %':[None,60],'Samples':[30,30]})
    today,history=timemap_sections(frame)
    assert today['Slot Return %'].iloc[0]==-7
    assert 'Baseline UP %' not in today and 'UP %' not in today
    assert history['UP %'].iloc[0]==60
    assert 'Slot Return %' not in history

def test_invalid_reference_never_fabricates_daily_percentage():
    for reference in [None,0,float('nan'),-1]:
        assert change_pct(2100,reference) is None
