"""Live reconciliation guard for the historical Time Map.

Historical clock-slot statistics remain useful evidence, but they must not be
rendered as an actionable bullish map when today's fresh intraday tape is moving
against them. This module only downgrades conflicting statuses; it never upgrades
a weak historical slot into BUY/ACTIVE from live price alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _minutes(label):
    try:
        h, m = str(label).split(':', 1)
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _live_direction(intraday, market_date):
    if intraday is None or intraday.empty:
        return {'usable': False, 'direction': 'UNKNOWN', 'ret_day': np.nan, 'ret_last': np.nan, 'bars': 0}
    d = intraday.copy()
    if 'ts' not in d.columns or 'close' not in d.columns:
        return {'usable': False, 'direction': 'UNKNOWN', 'ret_day': np.nan, 'ret_last': np.nan, 'bars': 0}
    d['ts'] = pd.to_datetime(d['ts'], utc=True, errors='coerce')
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    if 'open' in d.columns:
        d['open'] = pd.to_numeric(d['open'], errors='coerce')
    d = d.dropna(subset=['ts', 'close']).sort_values('ts')
    if d.empty:
        return {'usable': False, 'direction': 'UNKNOWN', 'ret_day': np.nan, 'ret_last': np.nan, 'bars': 0}
    d['wib'] = d['ts'].dt.tz_convert('Asia/Jakarta')
    d = d[d['wib'].dt.date.astype(str) == str(market_date)]
    if d.empty:
        return {'usable': False, 'direction': 'UNKNOWN', 'ret_day': np.nan, 'ret_last': np.nan, 'bars': 0}
    first_open = d.iloc[0].get('open', np.nan)
    if pd.isna(first_open) or not float(first_open):
        first_open = float(d.iloc[0]['close'])
    last = float(d.iloc[-1]['close'])
    ret_day = last / float(first_open) - 1.0
    ret_last = float(d['close'].pct_change().iloc[-1]) if len(d) >= 2 else 0.0
    ret_2 = last / float(d.iloc[-3]['close']) - 1.0 if len(d) >= 3 else ret_last
    # Require meaningful tape evidence. One tiny red candle is noise; a sustained
    # or material move is a divergence that must suppress stale bullish coloring.
    if ret_day <= -0.008 or ret_2 <= -0.006 or (ret_last <= -0.004 and ret_day < 0):
        direction = 'DOWN'
    elif ret_day >= 0.008 or ret_2 >= 0.006 or (ret_last >= 0.004 and ret_day > 0):
        direction = 'UP'
    else:
        direction = 'MIXED'
    return {'usable': True, 'direction': direction, 'ret_day': ret_day, 'ret_last': ret_last, 'bars': int(len(d)), 'last_price': last}


def reconcile_time_map_live(ticker, time_map, intraday, session):
    """Downgrade historical Time Map statuses when fresh live tape disagrees."""
    if time_map is None or time_map.empty:
        return time_map
    out = time_map.copy()
    out['historical_status'] = out.get('status', 'NEUTRAL')
    out['live_confirmation'] = 'N/A'

    session = dict(session or {})
    if not bool(session.get('live')):
        return out
    try:
        now = pd.Timestamp(session.get('wib'))
        if now.tzinfo is None:
            now = now.tz_localize('Asia/Jakarta')
    except Exception:
        now = pd.Timestamp.now(tz='Asia/Jakarta')
    market_date = str(session.get('market_date') or now.date())
    live = _live_direction(intraday, market_date)
    if not live.get('usable'):
        # No fresh same-day confirmation means no actionable green map.
        predictive = out['session'].isin(['Sesi I', 'Sesi II']) if 'session' in out.columns else pd.Series(True, index=out.index)
        out.loc[predictive & out['status'].isin(['ACTIVE', 'DEVELOPING']), 'status'] = 'NEUTRAL'
        out.loc[predictive, 'live_confirmation'] = 'NO LIVE CONFIRMATION'
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
        # Past slots stay historical evidence. Current/upcoming rows are the ones
        # a trader can misread as a live forecast, so reconcile those with tape.
        if slot < current_floor:
            out.at[idx, 'live_confirmation'] = 'HISTORICAL'
            continue
        hist = str(row.get('status') or 'NEUTRAL').upper()
        if direction == 'DOWN':
            out.at[idx, 'live_confirmation'] = 'DIVERGENCE'
            if hist == 'ACTIVE':
                out.at[idx, 'status'] = 'WAIT'
            elif hist == 'DEVELOPING':
                out.at[idx, 'status'] = 'NEUTRAL'
        elif direction == 'UP':
            out.at[idx, 'live_confirmation'] = 'MATCH' if hist in {'ACTIVE', 'DEVELOPING'} else 'MIXED'
            # Deliberately do not promote WAIT/NEUTRAL to ACTIVE from price alone.
        else:
            out.at[idx, 'live_confirmation'] = 'MIXED'
            if hist == 'ACTIVE':
                out.at[idx, 'status'] = 'DEVELOPING'

    ret_day = float(live.get('ret_day') or 0.0) * 100.0
    ret_last = float(live.get('ret_last') or 0.0) * 100.0
    suffix = f" Live {direction}: day {ret_day:+.2f}%, last bar {ret_last:+.2f}%."
    if 'note' in out.columns:
        mask = out['live_confirmation'].isin(['MATCH', 'MIXED', 'DIVERGENCE', 'NO LIVE CONFIRMATION'])
        out.loc[mask, 'note'] = out.loc[mask, 'note'].astype(str) + suffix
    return out
