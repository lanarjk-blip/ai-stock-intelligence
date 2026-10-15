from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def test_collection_state_prefix_keeps_bootstrap_metrics_separate(monkeypatch):
    import worker_base as w
    writes = {}
    monkeypatch.setattr(w, 'mark_engine_progress', lambda *a, **k: None)
    monkeypatch.setattr(w, 'fetch_one', lambda ticker: (ticker, True, 3, ''))
    monkeypatch.setattr(w.app, 'state_set', lambda key, value: writes.__setitem__(key, value))
    df = pd.DataFrame({'ticker': ['AAA', 'BBB']})
    ok, rows, errors = w.collect_fast(df, state_prefix='last_bootstrap_collection')
    assert ok == 2 and rows == 6 and errors == []
    assert writes['last_bootstrap_collection_ok'] == 2
    assert writes['last_bootstrap_collection_rows'] == 6
    assert 'last_collection' not in writes
    assert 'last_collection_ok' not in writes


def test_full_collection_status_honors_age_and_quality(monkeypatch):
    import worker_base as w
    cfg = dict(w.CFG)
    cfg.update({
        'full_collect_hours': 6,
        'data_quality_min_collection_ratio': 0.80,
        'data_quality_min_ready_ratio': 0.70,
        'data_quality_min_price_rows': 5000,
    })
    monkeypatch.setattr(w, 'CFG', cfg)
    state = {
        'last_collection_ok': '900',
    }
    monkeypatch.setattr(w.app, 'state_get', lambda key: state.get(key))
    monkeypatch.setattr(w, 'count_ready_symbols', lambda min_rows=60: 850)
    monkeypatch.setattr(w, 'count_price_rows', lambda: 100000)

    monkeypatch.setattr(w, 'stale', lambda key, hours: True)
    assert w.full_collection_status(957) == (True, 'STALE_OR_MISSING')

    monkeypatch.setattr(w, 'stale', lambda key, hours: False)
    due, reason = w.full_collection_status(957)
    assert due is False and reason == 'FRESH_CACHE'

    state['last_collection_ok'] = '100'
    due, reason = w.full_collection_status(957)
    assert due is True and reason.startswith('COLLECTION_COVERAGE_')


def test_cycle_interval_uses_market_session_cadence(monkeypatch):
    import worker_base as w
    cfg = dict(w.CFG)
    cfg.update({
        'market_open_cycle_minutes': 10,
        'post_close_cycle_minutes': 30,
        'off_market_cycle_minutes': 60,
        'refresh_minutes': 30,
    })
    monkeypatch.setattr(w, 'CFG', cfg)

    cases = [
        ({'live': True, 'status': 'SESSION_1'}, 600),
        ({'live': False, 'status': 'BREAK'}, 600),
        ({'live': False, 'status': 'CLOSED'}, 1800),
        ({'live': False, 'status': 'WEEKEND'}, 3600),
        ({'live': False, 'status': 'HOLIDAY'}, 3600),
        ({'live': False, 'status': 'PRE_MARKET'}, 1800),
    ]
    for status, expected in cases:
        monkeypatch.setattr(w.market_session, 'session_status', lambda cfg, now=None, value=status: value)
        assert w.cycle_interval_seconds() == expected


def test_real_market_session_examples_map_to_intended_intervals():
    import worker_base as w
    wib = ZoneInfo('Asia/Jakarta')
    assert w.cycle_interval_seconds(datetime(2026, 10, 15, 10, 0, tzinfo=wib)) == 600
    assert w.cycle_interval_seconds(datetime(2026, 10, 15, 12, 30, tzinfo=wib)) == 600
    assert w.cycle_interval_seconds(datetime(2026, 10, 15, 16, 30, tzinfo=wib)) == 1800
    assert w.cycle_interval_seconds(datetime(2026, 10, 18, 10, 0, tzinfo=wib)) == 3600
    assert w.cycle_interval_seconds(datetime(2026, 10, 15, 8, 0, tzinfo=wib)) == 1800


def test_worker_source_no_longer_forces_daily_retrain_or_warm_bootstrap():
    src = Path('worker_base.py').read_text(encoding='utf-8')
    assert 'maybe_train(\n        universe,\n        force=True' not in src
    assert 'state_prefix="last_bootstrap_collection"' in src
    assert 'full_collection_status(len(universe))' in src
    assert 'SKIP_FRESH_CACHE' in src
    assert 'cold_start = not bool(app.state_get("engine_last_run"))' in src
    assert 'warm runtime; full snapshot already exists' in src
    assert 'engine_next_cycle_interval_seconds' in src
