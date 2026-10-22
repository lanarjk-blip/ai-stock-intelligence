"""Live reconciliation guard for the historical Time Map.

Historical clock-slot statistics remain useful evidence, but they must not be
rendered as actionable when today's tape is stale or moving against them.
This module only confirms/downgrades historical evidence. It never upgrades a
weak historical slot into BUY/ACTIVE from live price alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAX_LIVE_AGE_MINUTES = 45.0


def _minutes(label):
    try:
        h, m = str(label).split(':', 1)
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _empty(reason='NO_INTRADAY'):
    return {
        'usable': False, 'fresh': False, 'direction': 'UNKNOWN',
        'ret_day': np.nan, 'ret_last': np.nan, 'ret_recent': np.nan,
        'bars': 0, 'last_price': np.nan, 'age_minutes': np.nan,
        'reason': reason,
    }


def _live_direction(intraday, market_date, now):
    if intraday is None or intraday.empty:
        return _empty('NO_INTRADAY')
    d = intraday.copy()
    if 'ts' not in d.columns or 'close' not in d.columns:
        return _empty('MISSING_TS_OR_CLOSE')
    d['ts'] = pd.to_datetime(d['ts'], utc=True, errors='coerce')
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    if 'open' in d.columns:
        d['open'] = pd.to_numeric(d['open'], errors='coerce')
    d = d.dropna(subset=['ts', 'close']).sort_values('ts')
    if d.empty:
        return _empty('INVALID_INTRADAY')
    d['wib'] = d['ts'].dt.tz_convert('Asia/Jakarta')
    d = d[d['wib'].dt.date.astype(str) == str(market_date)]
    if d.empty:
        return _empty('STALE_MARKET_DATE')

    last_ts = d.iloc[-1]['wib']
    try:
        age_minutes = max(0.0, (now - last_ts).total_seconds() / 60.0)
    except Exception:
        age_minutes = np.inf

    last = float(d.iloc[-1]['close'])
    # Critical gate: same-day data is NOT automatically live. A morning bar must
    # never certify a Sesi-II Time Map. During live market, >45 min is stale.
    if age_minutes > MAX_LIVE_AGE_MINUTES:
        out = _empty('STALE_INTRADAY_AGE')
        out.update({'bars': int(len(d)), 'last_price': last, 'age_minutes': age_minutes})
        return out

    first_open = d.iloc[0].get('open', np.nan)
    if pd.isna(first_open) or not float(first_open):
        first_open = float(d.iloc[0]['close'])
    ret_day = last / float(first_open) - 1.0
    ret_last = float(d['close'].pct_change().iloc[-1]) if len(d) >= 2 else 0.0
    ret_recent = last / float(d.iloc[-3]['close']) - 1.0 if len(d) >= 3 else ret_last

    # Conservative live classification. Sustained/material tape direction wins
    # over historical clock-slot tendency; tiny movement remains MIXED.
    if ret_day <= -0.008 or ret_recent <= -0.006 or (ret_last <= -0.004 and ret_day < 0):
        direction = 'DOWN'
    elif ret_day >= 0.008 or ret_recent >= 0.006 or (ret_last >= 0.004 and ret_day > 0):
        direction = 'UP'
    else:
        direction = 'MIXED'
    return {
        'usable': True, 'fresh': True, 'direction': direction,
        'ret_day': ret_day, 'ret_last': ret_last, 'ret_recent': ret_recent,
        'bars': int(len(d)), 'last_price': last, 'age_minutes': age_minutes,
        'reason': 'CURRENT_SESSION',
    }


def reconcile_time_map_live(ticker, time_map, intraday, session):
    """Reconcile historical Time Map with fresh current-session evidence."""
    if time_map is None or time_map.empty:
        return time_map
    out = time_map.copy()
    out['historical_status'] = out.get('status', 'NEUTRAL')
    out['live_confirmation'] = 'N/A'
    out['live_direction'] = 'UNKNOWN'
    out['live_fresh'] = False
    out['live_age_min'] = np.nan
    out['live_price'] = np.nan
    out['trusted'] = False

    session = dict(session or {})
    if not bool(session.get('live')):
        return out
    try:
        now = pd.Timestamp(session.get('wib'))
        if now.tzinfo is None:
            now = now.tz_localize('Asia/Jakarta')
        else:
            now = now.tz_convert('Asia/Jakarta')
    except Exception:
        now = pd.Timestamp.now(tz='Asia/Jakarta')
    market_date = str(session.get('market_date') or now.date())
    live = _live_direction(intraday, market_date, now)

    out['live_direction'] = str(live.get('direction') or 'UNKNOWN')
    out['live_fresh'] = bool(live.get('fresh'))
    out['live_age_min'] = live.get('age_minutes', np.nan)
    out['live_price'] = live.get('last_price', np.nan)

    predictive = out['session'].isin(['Sesi I', 'Sesi II']) if 'session' in out.columns else pd.Series(True, index=out.index)
    if not live.get('usable'):
        # No fresh same-session evidence means no actionable green map.
        out.loc[predictive & out['status'].isin(['ACTIVE', 'DEVELOPING']), 'status'] = 'WAIT'
        out.loc[predictive, 'live_confirmation'] = 'NO LIVE CONFIRMATION'
        reason = str(live.get('reason') or 'NO_LIVE_DATA')
        age = live.get('age_minutes', np.nan)
        age_text = f' age={float(age):.0f}m' if pd.notna(age) else ''
        if 'note' in out.columns:
            out.loc[predictive, 'note'] = out.loc[predictive, 'note'].astype(str) + f' Live blocked: {reason}{age_text}.'
        return out

    now_min = int(now.hour) * 60 + int(now.minute)
    current_floor = (now_min // 30) * 30
    direction = str(live.get('direction') or 'MIXED')
    for idx, row in out.iterrows():
        if str(row.get('session')) not in {'Sesi I', 'Sesi II'}:
            continue
        slot = _minutes(row.get('time'))
        if slot is None:
            continue
        # Completed slots remain preserved for audit. Current/upcoming slots are
        # reconciled against the actual tape so history cannot masquerade as live.
        if slot < current_floor:
            out.at[idx, 'live_confirmation'] = 'HISTORICAL'
            continue
        hist = str(row.get('status') or 'NEUTRAL').upper()
        if direction == 'DOWN':
            out.at[idx, 'live_confirmation'] = 'DIVERGENCE'
            if hist in {'ACTIVE', 'DEVELOPING'}:
                out.at[idx, 'status'] = 'WAIT'
        elif direction == 'UP':
            aligned = hist in {'ACTIVE', 'DEVELOPING'}
            out.at[idx, 'live_confirmation'] = 'MATCH' if aligned else 'MIXED'
            # Never promote WAIT/NEUTRAL from price alone.
            if aligned:
                out.at[idx, 'trusted'] = True
        else:
            out.at[idx, 'live_confirmation'] = 'MIXED'
            if hist in {'ACTIVE', 'DEVELOPING'}:
                out.at[idx, 'status'] = 'NEUTRAL'

    ret_day = float(live.get('ret_day') or 0.0) * 100.0
    ret_last = float(live.get('ret_last') or 0.0) * 100.0
    age = float(live.get('age_minutes') or 0.0)
    suffix = f' Live {direction}: day {ret_day:+.2f}%, last bar {ret_last:+.2f}%, age {age:.0f}m.'
    if 'note' in out.columns:
        mask = out['live_confirmation'].isin(['MATCH', 'MIXED', 'DIVERGENCE', 'NO LIVE CONFIRMATION'])
        out.loc[mask, 'note'] = out.loc[mask, 'note'].astype(str) + suffix
    return out
