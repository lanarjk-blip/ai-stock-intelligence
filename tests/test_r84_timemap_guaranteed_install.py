from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_app_direct_import_path_installs_timemap_live_layer():
    app = (ROOT / 'app.py').read_text(encoding='utf-8')
    ask = (ROOT / 'ai_stock_ask.py').read_text(encoding='utf-8')
    assert 'import ai_stock_ask' in app
    assert 'import ui_theme_patch' in ask
    assert 'ui_theme_patch.install()' in ask
    assert 'import timemap_ui_direct_live' in ask
    assert 'timemap_ui_direct_live.install()' in ask


def test_direct_live_layer_has_hard_market_day_filter():
    direct = (ROOT / 'timemap_ui_direct_live.py').read_text(encoding='utf-8')
    normalized = direct.replace('"', "'")
    assert "d['wib'].dt.date.astype(str) == market_date" in normalized
    assert 'DIRECT PROVIDER' in direct
    assert 'Checkpoint State' in direct
    assert 'NO BAR FOR ACTIVE MARKET DAY' in direct
