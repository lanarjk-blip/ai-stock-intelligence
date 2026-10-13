from datetime import datetime
from zoneinfo import ZoneInfo
import market_session

WIB=ZoneInfo("Asia/Jakarta")

def dt(y,m,d,h,minute=0): return datetime(y,m,d,h,minute,tzinfo=WIB)

def test_before_close_targets_same_market_date():
    assert market_session.next_trading_date({},dt(2026,10,13,8,0))=="2026-10-13"

def test_after_close_targets_next_trading_date():
    assert market_session.next_trading_date({},dt(2026,10,13,16,0))=="2026-10-14"

def test_friday_after_close_skips_weekend():
    assert market_session.next_trading_date({},dt(2026,10,16,16,0))=="2026-10-19"

def test_weekend_and_configured_holiday_are_skipped():
    cfg={"market_holidays":["2026-10-19"]}
    assert market_session.next_trading_date(cfg,dt(2026,10,17,10,0))=="2026-10-20"
