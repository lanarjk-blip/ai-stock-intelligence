import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import market_session
import timemap_checkpoint_today as cp
from time_map_live_guard import _live_direction, _session_window


def base_map(now, data, ticker='TEST'):
    """Execute the production calculation without starting the Streamlit worker."""
    tree = ast.parse(Path('app.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'intraday_time_map')
    calls = []
    ns = dict(pd=pd, np=np, CFGD={},
              market_session=SimpleNamespace(session_status=lambda cfg, **kw: market_session.session_status(cfg, now=kw.get('now', now))),
              _intraday_load=lambda _: data.copy(),
              fetch_chart_recover=lambda *a: calls.append(a) or pd.DataFrame(),
              save_intraday=lambda *a: None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), ns)
    return ns['intraday_time_map'](ticker), calls


def empty_bars():
    return pd.DataFrame(columns=['ts', 'open', 'high', 'low', 'close', 'volume'])


@pytest.mark.parametrize('ticker', ['MPXL', 'BBCA', 'ANTM', 'ZZZZ'])
def test_friday_empty_ticker_keeps_schedule_and_wait(ticker):
    out, calls = base_map(pd.Timestamp('2026-10-23 13:30', tz='Asia/Jakarta'), empty_bars(), ticker)
    by_time = out.set_index('time')
    assert by_time.loc['11:30', 'session'] == 'BREAK'
    assert by_time.loc['13:30', 'session'] == 'BREAK'
    assert by_time.loc['14:00', 'session'] == 'Sesi II'
    assert not out['trusted'].any()
    assert out['up_pct'].isna().all()
    assert calls


def test_thursday_schedule_is_preserved():
    out, _ = base_map(pd.Timestamp('2026-10-22 13:30', tz='Asia/Jakarta'), empty_bars())
    assert out.set_index('time').loc['13:30', 'session'] == 'Sesi II'


def test_large_stale_history_still_fetches():
    data = pd.DataFrame({'ts': pd.date_range('2026-10-19 02:00Z', periods=80, freq='30min'),
                         'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 1000.})
    _, calls = base_map(pd.Timestamp('2026-10-23 14:30', tz='Asia/Jakarta'), data)
    assert len(calls) == 1


def test_future_bars_never_count_as_live():
    now = pd.Timestamp('2026-10-23 14:05', tz='Asia/Jakarta')
    data = pd.DataFrame({'ts': pd.to_datetime(['2026-10-23T07:00Z', '2026-10-23T07:30Z']), 'open': [100, 100], 'close': [100, 120]})
    assert not _live_direction(data, '2026-10-23', now)['usable']
    assert _session_window(pd.Timestamp('2026-10-23 11:30', tz='Asia/Jakarta'))[0] is None
    assert _session_window(pd.Timestamp('2026-10-24 10:00', tz='Asia/Jakarta'))[0] is None


def test_provider_rejects_future_and_friday_break():
    data = pd.DataFrame({'ts': pd.to_datetime(['2026-10-23T02:00Z', '2026-10-23T06:30Z', '2026-10-23T07:30Z']), 'close': [100., 200., 300.]})
    base = SimpleNamespace(_session=lambda: ('BREAK', False, '2026-10-23', '2026-10-23T13:40:00+07:00'),
                           _provider_frame=lambda _: data, _cfg=lambda: {})
    state = cp._prepare_day(base, 'TEST')
    assert list(state['frame']['close']) == [100.]


def test_checkpoint_clock_and_friday_labels(monkeypatch):
    base = SimpleNamespace(_active_ticker=lambda _: 'TEST', _time_column=lambda _: 'Waktu')
    state = dict(market_date='2026-10-23', wib='2026-10-23T13:40:00+07:00',
                 frame=empty_bars(), fresh=False, market_status='BREAK', source='TEST', reason='NO DATA')
    monkeypatch.setattr(cp, '_prepare_day', lambda *_: state)
    history = pd.DataFrame([{'Waktu': t, 'Sesi': s} for t, s in [('11:30', 'Sesi I'), ('13:30', 'Sesi II'), ('14:00', 'Sesi II')]])
    out = cp._checkpoint_rows(history, None, base).set_index('Waktu')
    assert out.loc['13:30', 'Sesi'] == 'BREAK'
    assert out.loc['11:30', 'Sesi'] == 'BREAK'
    assert out.loc['14:00', 'Hari'] == 'Jumat'
    assert out.loc['14:00', 'Market Date'] == '2026-10-23'
    assert out.loc['14:00', 'Status'] == 'WAIT'
    assert out.loc['14:00', 'Kesegaran'] == 'STALE / NO DATA'
