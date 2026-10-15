import os
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault('AI_ENGINE_WORKER','1')
WIB=ZoneInfo('Asia/Jakarta')


def test_ui_checkpoint_uses_authoritative_regular_session_thursday():
    import app
    name,next_cp,note=app.intraday_checkpoint_status(datetime(2026,10,15,9,15,tzinfo=WIB))
    assert name=='SESI I'
    assert next_cp=='09:30'
    assert '09:00' in note


def test_ui_checkpoint_respects_friday_break():
    import app
    name,next_cp,note=app.intraday_checkpoint_status(datetime(2026,10,16,11,45,tzinfo=WIB))
    assert name=='BREAK'
    assert next_cp=='14:00'
    assert 'Istirahat' in note


def test_ui_checkpoint_respects_verified_idx_holiday():
    import app
    name,next_cp,note=app.intraday_checkpoint_status(datetime(2026,12,25,10,0,tzinfo=WIB))
    assert name=='HOLIDAY'
    assert next_cp.startswith('2026-12-28 09:00')
    assert 'Libur bursa' in note


def test_ui_checkpoint_regular_close_is_not_market_open():
    import app
    name,next_cp,note=app.intraday_checkpoint_status(datetime(2026,10,15,15,55,tzinfo=WIB))
    assert name=='AFTER-MARKET'
    assert next_cp.startswith('2026-10-16 09:00')
    assert 'tutup' in note.lower()


def test_ui_checkpoint_weekend_targets_next_trading_day():
    import app
    name,next_cp,note=app.intraday_checkpoint_status(datetime(2026,10,17,10,0,tzinfo=WIB))
    assert name=='WEEKEND'
    assert next_cp.startswith('2026-10-19 09:00')
    assert 'Tidak ada sesi' in note


def test_app_no_longer_marks_post_regular_period_as_open():
    from pathlib import Path
    src=Path('app.py').read_text(encoding='utf-8')
    assert "market_open=session_name in ['SESI I','SESI II']" in src
    assert "market_open=session_name in ['SESI I','SESI II','PRE-CLOSING','POST-TRADING']" not in src
    assert 'market_session.session_status(CFGD,now=now)' in src
