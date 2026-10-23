from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_timemap_live_refresh_is_not_blocked_by_existing_history():
    text = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
    assert "R81_TIME_MAP_LIVE_REFRESH" in text
    assert "if bool(_tm_session.get('live'))" in text
    assert "fetch_chart_recover(str(ticker)" in text
    refresh_block = text.split('R81_TIME_MAP_LIVE_REFRESH', 1)[1].split('if len(df)<60', 1)[0]
    assert "fetch_chart_recover" in refresh_block
    assert "save_intraday" in refresh_block
    assert "df=_intraday_load" in refresh_block


def test_timemap_live_truth_is_mobile_decision_first():
    text = (ROOT / "time_map_live_guard.py").read_text(encoding="utf-8")
    assert "def _decision_first" in text
    preferred = text.split("def _decision_first", 1)[1].split("def _session_window", 1)[0]
    assert preferred.index("'live_direction'") < preferred.index("'up_pct'")
    assert preferred.index("'live_confirmation'") < preferred.index("'up_pct'")
    assert preferred.index("'live_price'") < preferred.index("'up_pct'")


def test_timemap_guard_remains_global_not_symbol_specific():
    text = (ROOT / "time_map_live_guard.py").read_text(encoding="utf-8")
    for symbol in ("MPXL", "BBCA", "BBRI", "ANTM", "PTBA"):
        assert symbol not in text
