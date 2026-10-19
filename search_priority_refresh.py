"""R47 on-demand intraday refresh for manually searched tickers.

Search should not find a ticker and then leave the intelligence panel full of
'-' merely because that symbol was outside the current worker batch. This
module schedules one lightweight background refresh for the exact ticker the
user asked for. It never retrains the model and it is throttled per ticker.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

_LOCK = threading.Lock()
_LAST_ATTEMPT = {}
_RUNNING = set()


def _ticker(value):
    return str(value or "").upper().replace(".JK", "").strip()


def _history_count(app, ticker):
    try:
        con = app.dbcon()
        row = con.execute("SELECT COUNT(*) FROM intraday WHERE ticker=?", (ticker,)).fetchone()
        con.close()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def _company_for(app, ticker):
    try:
        universe, _ = app.load_universe()
        row = universe[universe.ticker.astype(str).str.upper() == ticker]
        return str(row.iloc[0].company) if not row.empty else ""
    except Exception:
        return ""


def _refresh(ticker):
    try:
        import app
        company = _company_for(app, ticker)
        full_days = max(5, int(app.CFGD.get("intraday_period_days", 60)))
        short_days = max(2, int(app.CFGD.get("intraday_refresh_period_days", 5)))
        period_days = full_days if _history_count(app, ticker) < 60 else short_days
        recover = getattr(app, "fetch_chart_recover", None)
        if callable(recover):
            value = recover(ticker, period_days, app.CFGD.get("intraday_interval", "30m"), company=company)
            if isinstance(value, tuple):
                df = next((item for item in value if hasattr(item, "empty")), None)
            else:
                df = value
        else:
            df = app.fetch_chart(ticker, period_days, app.CFGD.get("intraday_interval", "30m"))
        if df is None or df.empty:
            app.state_set(f"search_refresh_{ticker}", "EMPTY")
            return
        rows = int(app.save_intraday(df, ticker) or 0)
        app.state_set(f"search_refresh_{ticker}", f"READY rows={rows}")
        app.state_set(f"search_refresh_{ticker}_at", datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        try:
            import app
            app.state_set(f"search_refresh_{ticker}", f"ERROR {str(exc)[:240]}")
        except Exception:
            pass
    finally:
        with _LOCK:
            _RUNNING.discard(ticker)
            _LAST_ATTEMPT[ticker] = time.monotonic()


def request(ticker, min_interval_seconds=300):
    """Schedule a non-blocking refresh; return QUEUED/RUNNING/THROTTLED."""
    ticker = _ticker(ticker)
    if not ticker:
        return "INVALID"
    now = time.monotonic()
    with _LOCK:
        if ticker in _RUNNING:
            return "RUNNING"
        last = float(_LAST_ATTEMPT.get(ticker, 0.0))
        if last and now - last < float(min_interval_seconds):
            return "THROTTLED"
        _RUNNING.add(ticker)
        _LAST_ATTEMPT[ticker] = now
    try:
        import live_precision_patch
        live_precision_patch.record_search_priority(ticker)
    except Exception:
        pass
    threading.Thread(target=_refresh, args=(ticker,), name=f"asi-search-refresh-{ticker}", daemon=True).start()
    return "QUEUED"
