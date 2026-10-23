from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_r85_direct_live_has_explicit_active_ticker_sources():
    text=(ROOT/'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert 'def _active_ticker' in text
    assert 'st.query_params' in text
    assert 'stock_search_selection' in text
    assert 'stock_search_query' in text
    assert 'TICKER BELUM TERIKAT' in text


def test_r85_live_contract_is_visible_even_when_binding_fails():
    text=(ROOT/'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    for marker in ('Live Ticker','Market Date','Live Source','Live Price','Live Age m','Live Match'):
        assert marker in text
    assert 'DATA LIVE BELUM SEGAR' in text
    assert 'MARKET TIDAK LIVE' in text


def test_r85_market_day_filter_remains_hard_boundary():
    text=(ROOT/'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert 'd["wib"].dt.date.astype(str) == market_date' in text
    assert 'Previous-session' in text or 'previous-session' in text
