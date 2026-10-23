import pandas as pd
import ui_theme_patch as theme


def _frame():
    return pd.DataFrame([{
        'Waktu':'11:30','Sesi':'I','Horizon':'30m',
        'Live Ticker':'MPXL','Market Date':'2026-10-23','Market Status':'BREAK','Live Source':'DIRECT PROVIDER',
        'Live Price':123.0,'Live Return %':-1.2,'Live Momentum %':-0.4,'Volume Ratio':1.4,
        'Range Pos %':22.0,'Vs VWAP %':-0.7,'Live Score':-4,'Live Confidence %':82.0,
        'Live Direction':'DOWN','Live Confirmation':'DIVERGENCE • WAIT • BREAK','Live Match':'DIVERGENCE • WAIT • BREAK',
        'Match %':30.0,'Live Age m':42.0,'Live Bars':6,'Live Reason':'SESSION PAUSE',
        'Samples':10,'UP %':60.0,'Sideways %':10.0,'DOWN %':30.0,'Mean Return %':0.7,'Confidence %':55.0,'Status':'ACTIVE'
    }])


def test_r86_table_is_not_reaugmented_by_legacy_live_patch(monkeypatch):
    df=_frame()
    # If legacy live_metrics is touched, the test must fail. R86 table is authoritative.
    import live_precision_patch as lp
    monkeypatch.setattr(lp,'live_metrics',lambda *_a,**_k: (_ for _ in ()).throw(AssertionError('legacy patch called')))
    out=theme._augment_time_map(df)
    assert out.iloc[0]['Live Ticker']=='MPXL'
    assert out.iloc[0]['Live Direction']=='DOWN'
    assert out.iloc[0]['Live Confirmation']=='DIVERGENCE • WAIT • BREAK'


def test_r86_live_columns_stay_before_historical_columns():
    out=theme._decision_first(_frame())
    cols=list(out.columns)
    assert cols.index('Live Price') < cols.index('Samples')
    assert cols.index('Live Direction') < cols.index('UP %')
    assert cols.index('Live Confirmation') < cols.index('DOWN %')
    assert cols.index('Live Reason') < cols.index('Samples')
