from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_direct_ui_fallback_is_installed():
    runtime = (ROOT / 'runtime_health_ui_patch.py').read_text(encoding='utf-8')
    assert 'timemap_ui_direct_live.install()' in runtime


def test_direct_ui_fallback_uses_same_market_day_only():
    text = (ROOT / 'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    normalized = text.replace('"', "'")
    assert "d = d[d['wib'].dt.date.astype(str) == market_date]" in normalized
    assert "DIRECT PROVIDER" in text
    assert "DATA LIVE BELUM SEGAR" in text


def test_direct_ui_fallback_is_not_termux_dependent():
    text = (ROOT / 'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    assert 'from timemap_data import load' in text
    assert 'return load(ticker)' in text
    provider=(ROOT/'timemap_data.py').read_text(encoding='utf-8')
    assert 'query1.finance.yahoo.com' in provider
    assert 'query2.finance.yahoo.com' in provider
    assert 'import app' not in provider
    assert 'git pull' not in text
    assert 'termux' not in text.lower().replace('no termux update','')
