from datetime import datetime, timedelta, timezone
import sqlite3

import pandas as pd


def test_intraday_refresh_due_respects_live_session_market_date_and_interval(monkeypatch):
    import worker_base as w

    now = datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)  # 09:30 WIB
    state = {}
    monkeypatch.setattr(w.market_session, 'session_status', lambda cfg, now=None: {
        'live': True, 'status': 'SESSION_1', 'market_date': '2026-10-15',
    })
    monkeypatch.setattr(w.app, 'state_get', lambda key: state.get(key))

    due, reason = w.intraday_refresh_due(now=now)
    assert due is True and reason == 'NEW_MARKET_DATE'

    state['last_intraday_market_date'] = '2026-10-15'
    state['last_intraday_collection'] = (now - timedelta(minutes=5)).isoformat()
    due, reason = w.intraday_refresh_due(now=now)
    assert due is False and reason.startswith('FRESH_')

    state['last_intraday_collection'] = (now - timedelta(minutes=31)).isoformat()
    due, reason = w.intraday_refresh_due(now=now)
    assert due is True and reason == 'DUE'

    monkeypatch.setattr(w.market_session, 'session_status', lambda cfg, now=None: {
        'live': False, 'status': 'BREAK', 'market_date': '2026-10-15',
    })
    due, reason = w.intraday_refresh_due(now=now)
    assert due is False and reason == 'NOT_LIVE_BREAK'


def test_intraday_candidate_priority_includes_dynamic_and_h10_before_daily(tmp_path, monkeypatch):
    import worker_base as w

    db = tmp_path / 'market.db'
    monkeypatch.setattr(w.app, 'DB', str(db))
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE dynamic_opportunities (ticker TEXT, rank INTEGER)')
    con.execute("INSERT INTO dynamic_opportunities VALUES('DYN1',1)")
    con.execute('CREATE TABLE premarket_runs (market_date TEXT PRIMARY KEY,status TEXT,note TEXT)')
    con.execute("INSERT INTO premarket_runs VALUES('2026-10-15','LOCKED','H10_FINAL H10-R1')")
    con.execute('CREATE TABLE premarket_picks (market_date TEXT,rank INTEGER,ticker TEXT)')
    con.execute("INSERT INTO premarket_picks VALUES('2026-10-15',1,'H10A')")
    con.commit(); con.close()

    monkeypatch.setattr(w.market_session, 'session_status', lambda cfg, now=None: {
        'live': True, 'status': 'SESSION_1', 'market_date': '2026-10-15',
    })
    monkeypatch.setitem(w.CFG, 'intraday_symbols_per_cycle', 5)
    res = pd.DataFrame([
        {'ticker': 'AAA', 'relative_rank_score': 90, 'ai_score': 80},
        {'ticker': 'BBB', 'relative_rank_score': 85, 'ai_score': 82},
        {'ticker': 'CCC', 'relative_rank_score': 80, 'ai_score': 84},
        {'ticker': 'DDD', 'relative_rank_score': 75, 'ai_score': 86},
    ])
    tickers = w._intraday_candidate_tickers(res)
    assert tickers[:2] == ['DYN1', 'H10A']
    assert tickers == ['DYN1', 'H10A', 'AAA', 'BBB', 'CCC']


def test_refresh_intraday_candidates_is_bounded_and_persists_success(monkeypatch):
    import worker_base as w

    state = {}
    monkeypatch.setattr(w.market_session, 'session_status', lambda cfg, now=None: {
        'live': True, 'status': 'SESSION_1', 'market_date': '2026-10-15',
    })
    monkeypatch.setattr(w, 'intraday_refresh_due', lambda now=None: (True, 'DUE'))
    monkeypatch.setattr(w, '_intraday_candidate_tickers', lambda res, now=None: ['AAA', 'BBB'])
    monkeypatch.setattr(w.app, 'state_set', lambda key, value: state.__setitem__(key, str(value)))
    monkeypatch.setattr(w.app, 'state_get', lambda key: state.get(key))
    monkeypatch.setattr(w, 'log', lambda *args, **kwargs: None)
    monkeypatch.setattr(w, 'mark_engine_progress', lambda *args, **kwargs: None)

    frame = pd.DataFrame([{
        'ts': pd.Timestamp('2026-10-15T02:00:00Z'), 'open': 100.0, 'high': 101.0,
        'low': 99.0, 'close': 100.5, 'volume': 1000.0,
    }])
    monkeypatch.setattr(w, '_fetch_intraday_candidate', lambda ticker, company='': (ticker, frame.copy(), ''))
    saved = []
    monkeypatch.setattr(w.app, 'save_intraday', lambda df, ticker: saved.append(ticker) or len(df))

    result = w.refresh_intraday_candidates(pd.DataFrame([
        {'ticker': 'AAA', 'company': 'A'}, {'ticker': 'BBB', 'company': 'B'},
    ]), force=True)
    assert result == {'status': 'READY', 'ok': 2, 'rows': 2, 'errors': 0, 'tickers': 2}
    assert sorted(saved) == ['AAA', 'BBB']
    assert state['last_intraday_market_date'] == '2026-10-15'
    assert state['intraday_refresh_status'] == 'READY'
    assert 'last_intraday_collection' in state


def test_live_time_signal_fails_closed_when_intraday_is_stale(monkeypatch):
    import dynamic_engine as d

    monkeypatch.setattr(d, '_latest_intraday_live_price', lambda ticker, now=None: {
        'live_required': True, 'fresh': False, 'price': None, 'ts': '',
        'age_minutes': None, 'market_date': '2026-10-15',
    })
    monkeypatch.setattr(d.app, 'intraday_prediction', lambda ticker: (_ for _ in ()).throw(AssertionError('must not use stale live intraday')))
    signal = d._cached_time_signal('AAA')
    assert signal['intraday_fresh'] is False
    assert signal['entry_status'] == 'NEUTRAL'
    row = pd.Series({'decision': 'BUY', 'candidate_mode': 'STRICT'})
    assert d._state(row, signal, 99.0) == 'MONITOR'


def test_same_day_fresh_intraday_price_overlays_daily_close_in_dynamic_output(tmp_path, monkeypatch):
    import dynamic_engine as d
    import live_precision_patch as lp

    db = tmp_path / 'market.db'
    monkeypatch.setattr(d.app, 'DB', str(db))
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE state (key TEXT PRIMARY KEY,value TEXT)')
    con.commit(); con.close()

    monkeypatch.setattr(d, '_latest_intraday_live_price', lambda ticker, now=None: {
        'live_required': True, 'fresh': True, 'price': 123.0,
        'ts': '2026-10-15T02:30:00+00:00', 'age_minutes': 0.0,
        'market_date': '2026-10-15',
    })
    monkeypatch.setattr(d, '_cached_time_signal', lambda ticker: {
        'edge': 0.08, 'confidence': 0.8, 'horizon': 'EOD', 'entry_status': 'ACTIVE',
        'live_required': True, 'intraday_fresh': True,
        'intraday_at': '2026-10-15T02:30:00+00:00', 'price_source': 'LIVE_INTRADAY',
        'live_match': 'MATCH STRONG',
    })
    # R47 is a second, independent live-safety layer. Give it the same fresh
    # evidence as dynamic_engine so this test verifies the overlay path rather
    # than accidentally testing fail-closed behavior from an empty synthetic DB.
    monkeypatch.setattr(lp, 'live_metrics', lambda ticker, now=None: {
        'ticker': str(ticker), 'live_required': True, 'fresh': True,
        'bars': 4, 'market_date': '2026-10-15',
        'last_ts': '2026-10-15T02:30:00+00:00', 'age_minutes': 0.0,
        'open_price': 120.0, 'current_price': 123.0,
        'return_from_open_pct': 2.5, 'momentum_pct': 0.4,
        'volume_ratio': 1.4, 'range_position': 0.8,
        'relative_strength_pct': 1.0,
    })
    monkeypatch.setattr(lp, 'relative_strength_pct', lambda row, metrics=None: 1.0)
    monkeypatch.setattr(lp, 'market_breadth', lambda now=None, force=False: {
        'live': True, 'market_date': '2026-10-15', 'symbols': 50,
        'green': 30, 'red': 15, 'flat': 5,
        'green_ratio': 0.6, 'red_ratio': 0.3, 'defensive': False,
    })
    monkeypatch.setattr(d, 'update_opening_confirmation', lambda now=None: pd.DataFrame())

    res = pd.DataFrame([{
        'ticker': 'AAA', 'company': 'AAA', 'decision': 'BUY', 'decision_reason': 'unit',
        'ai_score': 90.0, 'relative_percentile': 0.99, 'relative_rank_score': 95.0,
        'jst_p_up': 0.72, 'jst_p_down': 0.12, 'jst_confidence': 0.80,
        'risk_reward_2': 2.0, 'risk': 'LOW', 'market_regime': 'BULLISH',
        'market_score': 70.0, 'close': 100.0, 'entry_low': 98.0, 'entry_high': 125.0,
        'stop_loss': 94.0, 'target_1': 130.0, 'target_2': 135.0,
    }])
    out = d.update(res, phase='FULL-UNIVERSE')
    assert len(out) == 1
    assert float(out.iloc[0]['close']) == 123.0
    assert out.iloc[0]['price_source'] == 'LIVE_INTRADAY'
    assert bool(out.iloc[0]['intraday_fresh']) is True
    assert out.iloc[0]['state'] == 'ENTRY READY'


def test_old_day_intraday_bar_is_not_considered_live(tmp_path, monkeypatch):
    import dynamic_engine as d

    db = tmp_path / 'market.db'
    monkeypatch.setattr(d.app, 'DB', str(db))
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE intraday (ticker TEXT,ts TEXT,close REAL)')
    con.execute("INSERT INTO intraday VALUES('AAA','2026-10-14T08:30:00+00:00',111.0)")
    con.commit(); con.close()
    monkeypatch.setattr(d.market_session, 'session_status', lambda cfg, now=None: {
        'live': True, 'status': 'SESSION_1', 'market_date': '2026-10-15',
        'wib': '2026-10-15T09:30:00+07:00',
    })
    info = d._latest_intraday_live_price('AAA')
    assert info['fresh'] is False
    assert info['price'] is None
