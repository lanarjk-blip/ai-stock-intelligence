from pathlib import Path

SRC = Path('timemap_ui_direct_live.py').read_text(encoding='utf-8')


def test_r86_hard_timemap_contract_present():
    required = [
        'Live Ticker', 'Market Date', 'Market Status', 'Live Price',
        'Live Return %', 'Live Momentum %', 'Live Direction',
        'Live Confirmation', 'Live Age m', 'Live Bars', 'Live Reason',
    ]
    for token in required:
        assert token in SRC
    assert '_asi_r86_hard_timemap' in SRC
    assert 'st.dataframe=dataframe' in SRC


def test_r86_market_day_isolation_and_break_visibility():
    assert 'd["wib"].dt.date.astype(str)==market_date' in SRC
    assert 'session_pause=status in {"BREAK","AFTER_MARKET"}' in SRC
    assert 'SESSION PAUSE' in SRC
    assert 'if status=="BREAK":conf=f"{conf} • BREAK"' in SRC


def test_r86_no_silent_historical_only_render():
    assert 'TICKER BELUM TERIKAT' in SRC
    assert 'NO BAR FOR ACTIVE MARKET DAY' in SRC
    assert 'WAIT • DATA NOT FRESH' in SRC
