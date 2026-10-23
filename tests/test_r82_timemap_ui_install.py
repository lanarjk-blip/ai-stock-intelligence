from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_r82_theme_installed_directly_in_ui_runtime():
    text=(ROOT/'runtime_health_ui_patch.py').read_text(encoding='utf-8')
    assert 'import ui_theme_patch' in text
    assert 'ui_theme_patch.install()' in text
    assert 'R82_TIME_MAP_UI' in text


def test_r82_timemap_decision_first_columns():
    text=(ROOT/'ui_theme_patch.py').read_text(encoding='utf-8')
    assert 'def _decision_first' in text
    block=text.split('def _decision_first',1)[1].split('def _augment_time_map',1)[0]
    assert block.index('"Live %"') < block.index('"Samples"')
    assert block.index('"Live Match"') < block.index('"UP %"')
    assert block.index('"Match %"') < block.index('"UP %"')


def test_r82_selected_ticker_binding_is_generic():
    text=(ROOT/'ui_theme_patch.py').read_text(encoding='utf-8')
    assert 'stock_search_selection' in text
    assert 'stock_search_query' in text
    assert 'split(" • ", 1)' in text
    for ticker in ('MPXL','BBCA','ANTM','BBRI'):
        assert ticker not in text
