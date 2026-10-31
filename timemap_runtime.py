"""Selected-ticker storage shared by the UI and the focused background worker.

No app import, full-universe scan, daily model training or order execution.
Stored bars keep their true timestamps; failed fetches never renew provenance.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from timemap_data import canonical_ticker, clean, storage_provenance


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT)')
    con.execute('''CREATE TABLE IF NOT EXISTS intraday(
        ticker TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,
        source TEXT,updated_at TEXT,PRIMARY KEY(ticker,ts))''')
    con.execute('''CREATE TABLE IF NOT EXISTS timemap_refresh_state(
        ticker TEXT PRIMARY KEY,trading_date TEXT NOT NULL,session TEXT NOT NULL,
        checked_at TEXT NOT NULL,status TEXT NOT NULL)''')
    return con


def persist_verified(path, ticker, frame, now):
    ticker = canonical_ticker(ticker)
    source = str(frame.attrs.get('source') or '')
    if (frame.empty or frame.attrs.get('fallback') or 'CACHE' in source.upper()
            or source.upper() in {'', 'UNKNOWN', 'UNAVAILABLE', 'NONE', 'NAN'}):
        return 0
    data = clean(frame, ticker, now)
    source, fetched_at = storage_provenance(frame, now)
    if fetched_at is None:
        return 0
    rows = [(ticker, r.ts.isoformat(), float(r.open), float(r.high), float(r.low),
             float(r.close), None if pd.isna(r.volume) else float(r.volume), source, fetched_at)
            for r in data.itertuples()]
    con = connect(path)
    try:
        # A slower concurrent request cannot overwrite a more recent observation.
        con.executemany('''INSERT INTO intraday VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker,ts) DO UPDATE SET open=excluded.open,high=excluded.high,
            low=excluded.low,close=excluded.close,volume=excluded.volume,
            source=excluded.source,updated_at=excluded.updated_at
            WHERE intraday.updated_at IS NULL
               OR julianday(excluded.updated_at)>=julianday(intraday.updated_at)''', rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


def mark_refresh(path, ticker, session, now, status):
    con = connect(path)
    try:
        con.execute('''INSERT INTO timemap_refresh_state VALUES(?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET trading_date=excluded.trading_date,
            session=excluded.session,checked_at=excluded.checked_at,status=excluded.status
            WHERE julianday(excluded.checked_at)>=julianday(timemap_refresh_state.checked_at)''',
            (canonical_ticker(ticker), session['market_date'], session['status'], now.isoformat(), status))
        con.commit()
    finally:
        con.close()


def due_tickers(path, now, session, limit=4):
    """Fair bounded watch queue, reset at each trading date/session boundary."""
    from timemap_forecast_ui import connect as forecast_connect
    forecast_connect(path).close()
    con = connect(path)
    try:
        rows = con.execute('''SELECT w.ticker FROM timemap_forecast_watch w
            LEFT JOIN timemap_refresh_state r USING(ticker)
            WHERE julianday(w.last_viewed) BETWEEN julianday(?) AND julianday(?)
              AND (r.ticker IS NULL OR r.trading_date!=? OR r.session!=?
                   OR julianday(r.checked_at)<=julianday(?))
            ORDER BY COALESCE(julianday(r.checked_at),0),w.ticker LIMIT ?''',
            ((now-pd.Timedelta(days=7)).isoformat(), now.isoformat(), session['market_date'],
             session['status'], (now-pd.Timedelta(seconds=90)).isoformat(), max(1,min(20,int(limit))))).fetchall()
        return [canonical_ticker(r[0]) for r in rows]
    finally:
        con.close()
