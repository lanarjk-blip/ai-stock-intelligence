from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_previous_day_bars_cannot_be_live_evidence():
    text = (ROOT / 'time_map_live_guard.py').read_text(encoding='utf-8')
    assert "d = d[d['wib'].dt.date.astype(str) == market_date]" in text
    assert "NEW_MARKET_DAY_NO_BARS" in text


def test_market_day_transition_is_persisted_per_ticker():
    text = (ROOT / 'time_map_live_guard.py').read_text(encoding='utf-8')
    assert "timemap_active_market_date_" in text
    assert "NEW_MARKET_DAY" in text
    assert "CURRENT_MARKET_DAY" in text


def test_new_day_is_fail_closed_until_live_bars_exist():
    text = (ROOT / 'time_map_live_guard.py').read_text(encoding='utf-8')
    assert "NEW DAY • WAIT LIVE DATA" in text
    assert "current_session_bars" in text
    assert "status'] = 'WAIT'" in text


def test_lifecycle_is_global_not_symbol_specific():
    text = (ROOT / 'time_map_live_guard.py').read_text(encoding='utf-8')
    for symbol in ('MPXL', 'BBCA', 'BBRI', 'ANTM', 'PTBA'):
        assert symbol not in text
