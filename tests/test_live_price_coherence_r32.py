import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

os.environ.setdefault('AI_ENGINE_WORKER','1')
WIB=ZoneInfo('Asia/Jakarta')


def test_hold_live_stale_time_signal_is_neutral_and_does_not_call_prediction(monkeypatch):
    import hold_engine as h
    monkeypatch.setattr(h,'_live_intraday_context',lambda ticker,now=None:{
        'live_required':True,'fresh':False,'price':None,'ts':'','age_minutes':None,'market_date':'2026-10-15'
    })
    monkeypatch.setattr(h.app,'intraday_prediction',lambda ticker:(_ for _ in ()).throw(AssertionError('stale live must not call prediction')))
    out=h._time('AAA')
    assert out['edge']==0.0
    assert out['confidence']==0.0
    assert out['intraday_fresh'] is False
    assert out['price_source']=='DAILY_CLOSE_STALE_INTRADAY'


def test_hold_offmarket_can_keep_historical_time_intelligence(monkeypatch):
    import hold_engine as h
    monkeypatch.setattr(h,'_live_intraday_context',lambda ticker,now=None:{
        'live_required':False,'fresh':False,'price':None,'ts':'','age_minutes':None,'market_date':'2026-10-16'
    })
    monkeypatch.setattr(h.app,'intraday_prediction',lambda ticker:pd.DataFrame([{
        'horizon':'EOD','edge':0.06,'confidence':0.7,'entry_status':'ACTIVE'
    }]))
    out=h._time('AAA')
    assert out['edge']==0.06
    assert out['confidence']==0.7
    assert out['price_source']=='HISTORICAL_INTRADAY'
    assert out['live_required'] is False


def test_hold_same_day_live_context_rejects_previous_day_bar(tmp_path,monkeypatch):
    import hold_engine as h
    db=tmp_path/'market.db'
    monkeypatch.setattr(h.app,'DB',str(db))
    con=sqlite3.connect(db)
    con.execute('CREATE TABLE intraday (ticker TEXT,ts TEXT,close REAL)')
    con.execute("INSERT INTO intraday VALUES('AAA','2026-10-14T08:30:00+00:00',111.0)")
    con.commit(); con.close()
    monkeypatch.setattr(h.market_session,'session_status',lambda cfg,now=None:{
        'live':True,'status':'SESSION_1','market_date':'2026-10-15','wib':'2026-10-15T09:30:00+07:00'
    })
    info=h._live_intraday_context('AAA')
    assert info['fresh'] is False
    assert info['price'] is None


def test_stock_display_price_uses_fresh_live_and_labels_source():
    import app
    live=app.resolve_stock_display_price(
        'AAA',100.0,
        session={'status':'SESSION_1','live':True,'calendar_verified':True,'market_date':'2026-10-15','wib':'2026-10-15T09:32:00+07:00'},
        live_info={'ticker':'AAA','source':'TEST PROVIDER','fresh':True,'price':123.0,'ts':'2026-10-15T02:30:00+00:00'}
    )
    assert live['price']==123.0
    assert live['label']=='Harga intraday'
    assert live['source']=='LIVE_INTRADAY'
    assert live['fresh'] is True


def test_stock_display_price_falls_back_explicitly_to_daily_close():
    import app
    stale=app.resolve_stock_display_price(
        'AAA',100.0,
        session={'status':'SESSION_1','live':True},
        live_info={'fresh':False,'price':None,'ts':''}
    )
    assert stale['price']==100.0
    assert stale['label']=='Daily close'
    assert stale['source']=='DAILY_CLOSE'
    assert stale['fresh'] is False


def test_hold_schema_has_live_price_provenance(tmp_path,monkeypatch):
    import hold_engine as h
    db=tmp_path/'market.db'
    monkeypatch.setattr(h.app,'DB',str(db))
    h.ensure_tables()
    con=sqlite3.connect(db)
    cols={r[1] for r in con.execute('PRAGMA table_info(hold_intelligence)').fetchall()}
    con.close()
    assert {'price_source','intraday_fresh','intraday_at','time_status'} <= cols


def test_ui_no_longer_calls_daily_close_harga_terakhir():
    from pathlib import Path
    src=Path('app.py').read_text(encoding='utf-8')
    assert "p1.metric('Harga terakhir'" not in src
    assert 'resolve_stock_display_price(selected_ticker,close)' in src
