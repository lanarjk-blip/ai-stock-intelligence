from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_direct_ui_fallback_is_installed():
    runtime = (ROOT / 'runtime_health_ui_patch.py').read_text(encoding='utf-8')
    assert 'timemap_ui_direct_live.install()' in runtime


def test_direct_ui_fallback_uses_same_market_day_only():
    text = (ROOT / 'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert "d = d[d['wib'].dt.date.astype(str) == market_date]" in text
    assert "DIRECT PROVIDER" in text
    assert "DATA LIVE BELUM SEGAR" in text


def test_direct_ui_fallback_is_not_termux_dependent():
    text = (ROOT / 'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert 'fetch_chart_recover' in text
    assert 'git pull' not in text
    assert 'termux' not in text.lower().replace('no termux update','')
