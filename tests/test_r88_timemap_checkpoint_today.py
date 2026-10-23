import pandas as pd
import timemap_checkpoint_today as cp


class DummySt:
    session_state = {}
    query_params = {}


class DummyBase:
    @staticmethod
    def _active_ticker(_st):
        return 'ZZZZ'

    @staticmethod
    def _time_column(frame):
        return 'Waktu' if 'Waktu' in frame.columns else None


def history_frame():
    return pd.DataFrame([
        {'Waktu':'09:00','Sesi':'Sesi I','Horizon':'30m','Samples':20,'UP %':60.0,'Sideways %':10.0,'DOWN %':30.0,'Mean Return %':0.5,'Confidence %':58.0,'Status':'ACTIVE'},
        {'Waktu':'09:30','Sesi':'Sesi I','Horizon':'30m','Samples':18,'UP %':45.0,'Sideways %':15.0,'DOWN %':40.0,'Mean Return %':0.1,'Confidence %':52.0,'Status':'NEUTRAL'},
        {'Waktu':'10:00','Sesi':'Sesi I','Horizon':'30m','Samples':17,'UP %':35.0,'Sideways %':15.0,'DOWN %':50.0,'Mean Return %':-0.2,'Confidence %':54.0,'Status':'WAIT'},
        {'Waktu':'14:00','Sesi':'Sesi II','Horizon':'30m','Samples':16,'UP %':50.0,'Sideways %':20.0,'DOWN %':30.0,'Mean Return %':0.2,'Confidence %':51.0,'Status':'NEUTRAL'},
    ])


def day_state(date, closes):
    ts=pd.to_datetime([f'{date} 02:00:00Z',f'{date} 02:30:00Z',f'{date} 03:00:00Z'])
    frame=pd.DataFrame({
        'ts':ts,
        'open':[100.0,closes[0],closes[1]],
        'high':[max(100.0,closes[0]),max(closes[:2]),max(closes)],
        'low':[min(100.0,closes[0]),min(closes[:2]),min(closes)],
        'close':closes,
        'volume':[1000.0,1400.0,1800.0],
    })
    frame['wib']=frame.ts.dt.tz_convert('Asia/Jakarta')
    frame['minute']=frame.wib.dt.hour*60+frame.wib.dt.minute
    return {
        'ticker':'ZZZZ','market_date':date,'market_status':'BREAK','live_required':False,
        'wib':f'{date}T13:00:00+07:00','fresh':True,'age_min':30.0,'reason':'SESSION PAUSE',
        'source':'DIRECT PROVIDER','frame':frame,
    }


def test_two_market_days_have_different_realized_checkpoint_tape(monkeypatch):
    monkeypatch.setattr(cp,'_prepare_day',lambda _base,_ticker:day_state('2026-10-22',[101.0,103.0,104.0]))
    up=cp._checkpoint_rows(history_frame(),DummySt(),DummyBase())
    monkeypatch.setattr(cp,'_prepare_day',lambda _base,_ticker:day_state('2026-10-23',[99.0,97.0,96.0]))
    down=cp._checkpoint_rows(history_frame(),DummySt(),DummyBase())

    up_real=up[up['Checkpoint State']=='REALIZED TODAY'].reset_index(drop=True)
    down_real=down[down['Checkpoint State']=='REALIZED TODAY'].reset_index(drop=True)
    assert list(up_real['Live Price']) != list(down_real['Live Price'])
    assert set(up_real['Market Date']) == {'2026-10-22'}
    assert set(down_real['Market Date']) == {'2026-10-23'}
    assert up_real.iloc[-1]['Live Direction'] == 'UP'
    assert down_real.iloc[-1]['Live Direction'] == 'DOWN'


def test_future_checkpoint_never_uses_morning_bar(monkeypatch):
    monkeypatch.setattr(cp,'_prepare_day',lambda _base,_ticker:day_state('2026-10-23',[99.0,97.0,96.0]))
    out=cp._checkpoint_rows(history_frame(),DummySt(),DummyBase())
    future=out[out['Waktu']=='14:00'].iloc[0]
    assert future['Checkpoint State']=='WAIT FUTURE'
    assert pd.isna(future['Live Price'])
    assert future['Live Confirmation']=='WAIT • CHECKPOINT BELUM TERJADI'


def test_today_rows_do_not_reuse_historical_percentages(monkeypatch):
    monkeypatch.setattr(cp,'_prepare_day',lambda _base,_ticker:day_state('2026-10-23',[99.0,97.0,96.0]))
    out=cp._checkpoint_rows(history_frame(),DummySt(),DummyBase())
    realized=out[out['Checkpoint State']=='REALIZED TODAY'].iloc[0]
    assert pd.isna(realized['UP %'])
    assert pd.isna(realized['DOWN %'])
    assert realized['Baseline UP %']==60.0
    assert realized['Data Type']=='TODAY CHECKPOINT'


def test_startup_installs_checkpoint_layer_after_base_layer():
    text=open('ai_stock_ask.py',encoding='utf-8').read()
    assert text.index('timemap_ui_direct_live.install()') < text.index('timemap_checkpoint_today.install()')


def test_checkpoint_module_is_universal_and_market_day_scoped():
    source=open(cp.__file__,encoding='utf-8').read()
    for symbol in ('MPXL','BBCA','BBRI','ANTM','PTBA'):
        assert symbol not in source
    assert 'data[data["wib"].dt.date.astype(str) == str(market_date)]' in source
    assert 'NO BAR FOR ACTIVE MARKET DAY' in source
    assert 'WAIT FUTURE' in source
