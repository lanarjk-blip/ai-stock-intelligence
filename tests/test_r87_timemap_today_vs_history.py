from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_r87_current_market_day_boundary_remains_in_base_layer():
    source=(ROOT/'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert 'NO BAR FOR ACTIVE MARKET DAY' in source
    assert 'd = d[d["wib"].dt.date.astype(str) == market_date].copy()' in source
    assert 'HISTORICAL BASELINE' in source


def test_r88_supersedes_single_today_row_with_checkpoint_map():
    source=(ROOT/'timemap_checkpoint_today.py').read_text(encoding='utf-8')
    assert 'TODAY CHECKPOINT' in source
    assert 'REALIZED TODAY' in source
    assert 'WAIT FUTURE' in source
    assert 'Baseline UP %' in source
    assert '"Status": "WAIT"' in source
    assert 'data[data["wib"].dt.date.astype(str) == str(market_date)]' in source


def test_missing_today_data_is_fail_closed_not_historical_live():
    source=(ROOT/'timemap_checkpoint_today.py').read_text(encoding='utf-8')
    assert 'NO LIVE DATA' in source
    assert 'WAIT • DATA NOT FRESH' in source
    assert 'NO BAR FOR ACTIVE MARKET DAY' in source
    assert '"UP %": None' in source
    assert '"DOWN %": None' in source


def test_timemap_layers_have_no_symbol_specific_logic():
    combined=(ROOT/'timemap_ui_direct_live.py').read_text(encoding='utf-8')+(ROOT/'timemap_checkpoint_today.py').read_text(encoding='utf-8')
    for symbol in ('MPXL','BBCA','BBRI','ANTM','PTBA'):
        assert symbol not in combined
    assert 'if len(df)<60' not in (ROOT/'timemap_checkpoint_today.py').read_text(encoding='utf-8')
