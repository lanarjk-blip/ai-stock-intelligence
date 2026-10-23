from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_selected_ticker_context_is_trusted_globally():
    text = (ROOT / "ui_theme_patch.py").read_text(encoding="utf-8")
    assert '"stock_search_selection"' in text
    assert 'text.split("•", 1)[0].strip()' in text
    assert 'for value in (qp.get("ticker"), qp.get("stock"), qp.get("symbol"))' in text
    # Explicit context must not require replica DB membership.
    explicit_block = text.split('def _current_ticker_hint():',1)[1].split('# Only the broad scalar scan',1)[0]
    assert '_ticker_exists' not in explicit_block


def test_no_symbol_specific_special_cases():
    text = (ROOT / "ui_theme_patch.py").read_text(encoding="utf-8")
    for symbol in ("MPXL", "BBCA", "BBRI", "ANTM", "PTBA"):
        assert symbol not in text
