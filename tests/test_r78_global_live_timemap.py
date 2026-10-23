from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_guard_is_ticker_agnostic():
    text = (ROOT / "time_map_live_guard.py").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "ticker-agnostic" in lowered
    for symbol in ("MPXL", "BBCA", "BBRI", "ANTM", "PTBA"):
        assert symbol not in text


def test_ui_theme_prioritizes_live_columns_and_guards_entry_window():
    text = (ROOT / "ui_theme_patch.py").read_text(encoding="utf-8")
    assert '"Live %", "Live Match", "Match %"' in text
    assert "_guard_entry_window_message" in text
    assert "Entry Window historis" in text
    assert "_ticker_exists" in text
    assert "selected_ticker" in text
    assert "dynamic_opportunities" in text


def test_external_ui_always_installs_time_map_theme_guard():
    text = (ROOT / "runtime_health_ui_patch.py").read_text(encoding="utf-8")
    assert "import ui_theme_patch" in text
    assert "ui_theme_patch.install()" in text
