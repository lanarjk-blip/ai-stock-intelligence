from datetime import datetime
from zoneinfo import ZoneInfo

import timemap_ui_direct_live as tm


def test_mpxl_current_market_day_live_probe():
    today = datetime.now(ZoneInfo('Asia/Jakarta')).date().isoformat()
    live = tm._live('MPXL')
    print('MPXL_R86_LIVE_PROBE', live)
    assert live['market_date'] == today
    assert live['bars'] > 0, live
    assert live['price'] is not None and float(live['price']) > 0, live
    assert live['return_pct'] is not None, live
    assert live['momentum_pct'] is not None, live
    assert live['direction'] in {'UP', 'DOWN', 'SIDEWAYS'}, live
    assert live['source'] == 'UI_DIRECT', live
    assert live['reason'] not in {'NO BAR FOR ACTIVE MARKET DAY', 'NO INTRADAY BARS'}, live
