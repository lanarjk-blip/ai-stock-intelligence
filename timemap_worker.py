"""Focused Time Map maintenance; reached through the existing supervised entrypoint."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from market_session import session_status
from timemap_data import load, bar_freshness
from timemap_runtime import connect, due_tickers, persist_verified, mark_refresh
from timemap_forecast_ui import prepare_watched, settle_pending

BASE = Path(__file__).parent


def _state(path, **values):
    con = connect(path)
    try:
        con.executemany('INSERT OR REPLACE INTO state VALUES(?,?)',
                        [(k, str(v)) for k, v in values.items()])
        con.commit()
    finally:
        con.close()


def run_once(path, now=None, cfg=None, loader=None):
    now = pd.Timestamp.now(tz='Asia/Jakarta') if now is None else pd.Timestamp(now).tz_convert('Asia/Jakarta')
    cfg = cfg or {}
    loader = loader or load
    session = session_status(cfg, now=now)
    connect(path).close()
    _state(path, runtime_workload='timemap', engine_cycle_started_at=now.isoformat(),
           engine_cycle_status='RUNNING', engine_progress_at=now.isoformat(),
           engine_progress_stage='TIME_MAP_WATCH', engine_cycle_error='',
           data_quality_status='BLOCKED',
           data_quality_reasons='Legacy scanner disabled; Time Map validates its own candle evidence')
    # No all-market scans. Outside verified trading days, only settle stored bars.
    fetch = (session.get('calendar_verified')
             and session['status'] not in {'WEEKEND','HOLIDAY','CALENDAR_UNVERIFIED'}
             and '07:45' <= now.strftime('%H:%M') < '16:30')
    tickers = due_tickers(path,now,session,limit=cfg.get('timemap_worker_batch_size',4)) if fetch else []

    def refresh(ticker):
        try:
            tape = loader(ticker,now=now)
            persist_verified(path,ticker,tape,now)
            status = bar_freshness(tape,now)
        except Exception as exc:
            # Failures advance retry bookkeeping, never the candle or forecast.
            status = 'DATA UNAVAILABLE: '+type(exc).__name__
        mark_refresh(path,ticker,session,now,status)
        return status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(refresh,tickers))
    prepared = prepare_watched(path,now,cfg)
    settled = settle_pending(path,now,cfg)
    _state(path, engine_cycle_status='IDLE', engine_last_run=now.isoformat(),
           engine_cycle_completed_at=now.isoformat(), engine_progress_at=now.isoformat(),
           engine_progress_stage='TIME_MAP_DONE', timemap_watched_checked=len(tickers),
           timemap_new_outcomes=settled['new_outcomes'],
           timemap_fetch_status=json.dumps(dict(zip(tickers,statuses))))
    return {'mode':'timemap','checked':len(tickers),'statuses':dict(zip(tickers,statuses)),
            'prepared':prepared['recorded'],'new_outcomes':settled['new_outcomes']}


def interval_seconds(now=None,cfg=None):
    session = session_status(cfg,now=now)
    if session['live'] or session['status']=='PRE_MARKET':
        return 90
    if session['status'] in {'BREAK','CLOSED'}:
        return 300
    return 1800


def run_forever():
    import local_runtime_health
    local_runtime_health.start_worker_monitor()  # also claims singleton/lease without sitecustomize
    # Idempotent fallback for Python entrypoints that did not load sitecustomize.
    import threading
    import persistent_bridge
    if persistent_bridge.postgres_enabled() and 'asi-persist-bridge' not in {t.name for t in threading.enumerate()}:
        import runtime_supervisor
        persistent_bridge.restore_worker_checkpoint_if_needed()
        persistent_bridge.start_worker_bridge(runtime_supervisor.CLAIM)
    cfg = json.loads((BASE/'config.json').read_text())
    path = BASE/'db'/'market.db'
    print('[TIME_MAP] mode=timemap; legacy scanner/Pre-Market/Hold/training disabled',flush=True)
    while True:
        started = time.monotonic()
        try:
            print('[TIME_MAP] '+json.dumps(run_once(path,cfg=cfg)),flush=True)
        except Exception as exc:
            now = pd.Timestamp.now(tz='UTC').isoformat()
            _state(path,engine_cycle_status='ERROR',engine_cycle_error=type(exc).__name__,
                   engine_cycle_error_at=now,engine_progress_at=now,engine_progress_stage='TIME_MAP_ERROR')
            print('[TIME_MAP] cycle failed: '+type(exc).__name__,flush=True)
        interval = interval_seconds(cfg=cfg)
        _state(path,engine_next_cycle_interval_seconds=interval)
        time.sleep(max(30,interval-(time.monotonic()-started)))
