"""Direct live Time Map fallback for the Streamlit/APK UI.

The UI first uses replicated intraday state. If that state is missing or stale,
this module fetches only the currently opened IDX ticker directly from the
provider and computes same-market-day live evidence in memory. No Termux update
is required for this UI fallback.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

_INSTALLED = False


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _direct_metrics(ticker):
    import app
    import market_session

    session = dict(market_session.session_status(app.CFGD) or {})
    market_date = str(session.get('market_date') or '')
    out = {
        'ticker': str(ticker or '').upper().replace('.JK',''),
        'live_required': bool(session.get('live')),
        'fresh': False,
        'bars': 0,
        'market_date': market_date,
        'last_ts': '',
        'age_minutes': None,
        'open_price': None,
        'current_price': None,
        'return_from_open_pct': None,
        'momentum_pct': None,
        'volume_ratio': None,
        'range_position': None,
        'source': 'UI_DIRECT',
    }
    if not out['live_required']:
        return out
    try:
        value = app.fetch_chart_recover(
            out['ticker'],
            max(2, int(app.CFGD.get('intraday_refresh_period_days', 5))),
            app.CFGD.get('intraday_interval', '30m'),
        )
        if isinstance(value, tuple):
            df = next((v for v in value if isinstance(v, pd.DataFrame)), pd.DataFrame())
        else:
            df = value if isinstance(value, pd.DataFrame) else pd.DataFrame()
        if df.empty or 'ts' not in df.columns or 'close' not in df.columns:
            return out
        d = df.copy()
        d['ts'] = pd.to_datetime(d['ts'], utc=True, errors='coerce')
        for col in ('open','high','low','close','volume'):
            if col in d.columns:
                d[col] = pd.to_numeric(d[col], errors='coerce')
        d = d.dropna(subset=['ts','close']).sort_values('ts')
        if d.empty:
            return out
        d['wib'] = d['ts'].dt.tz_convert('Asia/Jakarta')
        # Hard market-day boundary. Yesterday can never satisfy today's live state.
        d = d[d['wib'].dt.date.astype(str) == market_date].copy()
        if d.empty:
            return out

        now = pd.Timestamp(session.get('wib'))
        if now.tzinfo is None:
            now = now.tz_localize('Asia/Jakarta')
        else:
            now = now.tz_convert('Asia/Jakarta')
        last = d.iloc[-1]
        first = d.iloc[0]
        age = max(0.0, (now.tz_convert('UTC') - last['ts']).total_seconds()/60.0)
        stale_limit = max(10, int(app.CFGD.get('intraday_live_stale_minutes', 30)))
        op = _num(first.get('open'), _num(first.get('close')))
        cur = _num(last.get('close'))
        ret = ((cur/op)-1.0)*100.0 if cur and op else None
        if len(d) >= 2:
            prev = _num(d.iloc[-2].get('close'))
            mom = ((cur/prev)-1.0)*100.0 if cur and prev else 0.0
        else:
            mom = 0.0
        vols = pd.to_numeric(d.get('volume'), errors='coerce').dropna() if 'volume' in d.columns else pd.Series(dtype=float)
        baseline = _num(vols.iloc[:-1].tail(6).median(), 0.0) if len(vols) >= 3 else 0.0
        vr = float(vols.iloc[-1])/baseline if len(vols) >= 3 and baseline > 0 else None
        hi = _num(d['high'].max()) if 'high' in d.columns else cur
        lo = _num(d['low'].min()) if 'low' in d.columns else cur
        pos = ((cur-lo)/(hi-lo)) if cur is not None and hi is not None and lo is not None and hi > lo else 0.5
        out.update({
            'fresh': bool(age <= stale_limit),
            'bars': int(len(d)),
            'last_ts': last['ts'].isoformat(),
            'age_minutes': age,
            'open_price': op,
            'current_price': cur,
            'return_from_open_pct': ret,
            'momentum_pct': mom,
            'volume_ratio': vr,
            'range_position': pos,
        })
        return out
    except Exception:
        return out


def install():
    global _INSTALLED
    if _INSTALLED:
        return True
    import ui_theme_patch as theme
    import live_precision_patch as lp

    original = theme._augment_time_map
    if getattr(original, '_asi_direct_live', False):
        _INSTALLED = True
        return True

    def augmented(df):
        out = original(df)
        if not isinstance(out, pd.DataFrame) or out.empty:
            return out
        cols = set(map(str, out.columns))
        if not ({'UP %','DOWN %','Samples'} <= cols):
            return out
        ticker = theme._current_ticker_hint()
        time_col = theme._time_column(out)
        if not ticker or time_col is None:
            return theme._decision_first(out)

        need_direct = True
        if 'Live Match' in out.columns:
            values = out['Live Match'].astype(str).str.strip()
            need_direct = not values.isin(['MATCH','MATCH STRONG','DIVERGENCE','MIXED']).any()
        if not need_direct:
            return theme._decision_first(out)

        metrics = _direct_metrics(ticker)
        if 'Live %' not in out.columns:
            out['Live %'] = None
        if 'Live Match' not in out.columns:
            out['Live Match'] = ''
        if 'Match %' not in out.columns:
            out['Match %'] = None
        if 'Live Source' not in out.columns:
            out['Live Source'] = ''
        if 'Market Date' not in out.columns:
            out['Market Date'] = ''

        now = pd.Timestamp.now(tz='Asia/Jakarta')
        slot = f"{now.hour:02d}:{0 if now.minute < 30 else 30:02d}"
        matched = None
        for idx, value in out[time_col].items():
            m = re.search(r'(\d{1,2}):(\d{2})', str(value or ''))
            if m and f"{int(m.group(1)):02d}:{m.group(2)}" == slot:
                matched = idx
                break
        if matched is None:
            return theme._decision_first(out)

        out.at[matched, 'Market Date'] = metrics.get('market_date') or ''
        out.at[matched, 'Live Source'] = 'DIRECT PROVIDER'
        if not metrics.get('fresh'):
            out.at[matched, 'Live Match'] = 'DATA LIVE BELUM SEGAR'
            return theme._decision_first(out)

        up = out.at[matched, 'UP %']
        down = out.at[matched, 'DOWN %']
        metrics['relative_strength_pct'] = lp.relative_strength_pct({'ticker': ticker}, metrics)
        status, score = lp.map_live_match(up, down, metrics)
        out.at[matched, 'Live %'] = metrics.get('return_from_open_pct')
        out.at[matched, 'Live Match'] = status
        out.at[matched, 'Match %'] = score
        return theme._decision_first(out)

    augmented._asi_direct_live = True
    theme._augment_time_map = augmented
    _INSTALLED = True
    return True
