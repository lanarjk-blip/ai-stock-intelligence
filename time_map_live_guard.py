"""Authoritative Time Map live reconciliation with explicit market-day lifecycle.

Historical slot statistics remain long-lived evidence. Live state is strictly scoped
to the active IDX market date and may never leak from the previous trading day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAX_LIVE_AGE_MINUTES = 20.0
MIN_LIVE_BARS = 2
MIN_TRUST_SAMPLES = 20
MIN_TRUST_CONFIDENCE = 55.0
MIN_TRUST_EDGE = 10.0


def _minutes(label):
    try:
        h, m = str(label).split(':', 1)
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _empty(reason='NO_INTRADAY', market_date=''):
    return {
        'usable': False, 'fresh': False, 'direction': 'UNKNOWN',
        'ret_day': np.nan, 'ret_last': np.nan, 'ret_recent': np.nan,
        'bars': 0, 'last_price': np.nan, 'age_minutes': np.nan,
        'reason': reason, 'session_bars': 0, 'market_date': str(market_date or ''),
    }


def _decision_first(out):
    if out is None or not isinstance(out, pd.DataFrame) or out.empty:
        return out
    preferred = [
        'time', 'session', 'market_date', 'lifecycle', 'live_direction',
        'live_confirmation', 'live_price', 'live_age_min', 'live_fresh',
        'current_session_bars', 'status', 'trusted', 'horizon', 'samples',
        'up_pct', 'sideways_pct', 'down_pct', 'mean_return_pct',
        'confidence_pct', 'historical_status', 'note',
    ]
    front = [c for c in preferred if c in out.columns]
    rest = [c for c in out.columns if c not in front]
    return out[front + rest]


def _session_window(now):
    dow = int(now.dayofweek)
    minute = int(now.hour) * 60 + int(now.minute)
    if dow >= 5:
        return None, None, 'OUTSIDE_REGULAR_SESSION'
    if dow == 4:
        if 9 * 60 <= minute < 11 * 60 + 30:
            return 9 * 60, 11 * 60 + 30, 'SESI_I'
        if 14 * 60 <= minute < 15 * 60 + 50:
            return 14 * 60, 15 * 60 + 50, 'SESI_II'
    else:
        if 9 * 60 <= minute < 12 * 60:
            return 9 * 60, 12 * 60, 'SESI_I'
        if 13 * 60 + 30 <= minute < 15 * 60 + 50:
            return 13 * 60 + 30, 15 * 60 + 50, 'SESI_II'
    return None, None, 'OUTSIDE_REGULAR_SESSION'


def _live_direction(intraday, market_date, now):
    market_date = str(market_date or '')
    if intraday is None or intraday.empty:
        return _empty('NO_INTRADAY', market_date)
    d = intraday.copy()
    if 'ts' not in d.columns or 'close' not in d.columns:
        return _empty('MISSING_TS_OR_CLOSE', market_date)
    d['ts'] = pd.to_datetime(d['ts'], utc=True, errors='coerce')
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    if 'open' in d.columns:
        d['open'] = pd.to_numeric(d['open'], errors='coerce')
    d = d.dropna(subset=['ts', 'close']).sort_values('ts')
    if d.empty:
        return _empty('INVALID_INTRADAY', market_date)

    d = d[(d['ts'] <= now.tz_convert('UTC')) & (d['close'] > 0)].drop_duplicates('ts',keep='last')
    d['wib'] = d['ts'].dt.tz_convert('Asia/Jakarta')
    # R83 hard boundary: previous market-day bars are never live evidence.
    d = d[d['wib'].dt.date.astype(str) == market_date].copy()
    if d.empty:
        return _empty('NEW_MARKET_DAY_NO_BARS', market_date)

    start_min, end_min, session_name = _session_window(now)
    if start_min is None:
        return _empty('OUTSIDE_REGULAR_SESSION', market_date)
    d['minute'] = d['wib'].dt.hour * 60 + d['wib'].dt.minute
    s = d[(d['minute'] >= start_min) & (d['minute'] <= end_min)].copy()
    if s.empty:
        out = _empty('NO_CURRENT_SESSION_DATA', market_date)
        out['bars'] = int(len(d))
        return out

    last_ts = s.iloc[-1]['wib']
    age_minutes = max(0.0, (now - last_ts).total_seconds() / 60.0)
    last = float(s.iloc[-1]['close'])
    if age_minutes > MAX_LIVE_AGE_MINUTES:
        out = _empty('STALE_INTRADAY_AGE', market_date)
        out.update({'bars': int(len(d)), 'session_bars': int(len(s)), 'last_price': last, 'age_minutes': age_minutes})
        return out
    if len(s) < MIN_LIVE_BARS:
        out = _empty('INSUFFICIENT_CURRENT_SESSION_BARS', market_date)
        out.update({'bars': int(len(d)), 'session_bars': int(len(s)), 'last_price': last, 'age_minutes': age_minutes})
        return out

    first_open = s.iloc[0].get('open', np.nan)
    if pd.isna(first_open) or not float(first_open):
        first_open = float(s.iloc[0]['close'])
    ret_session = last / float(first_open) - 1.0
    ret_last = float(s['close'].pct_change().iloc[-1])
    ret_recent = last / float(s.iloc[max(0, len(s)-3)]['close']) - 1.0
    composite = 0.50 * ret_session + 0.30 * ret_recent + 0.20 * ret_last
    if composite <= -0.0025 and ret_session < 0:
        direction = 'DOWN'
    elif composite >= 0.0025 and ret_session > 0:
        direction = 'UP'
    else:
        direction = 'MIXED'
    return {
        'usable': True, 'fresh': True, 'direction': direction,
        'ret_day': ret_session, 'ret_last': ret_last, 'ret_recent': ret_recent,
        'bars': int(len(d)), 'session_bars': int(len(s)), 'last_price': last,
        'age_minutes': age_minutes, 'reason': session_name, 'market_date': market_date,
    }


def _historical_trust(row):
    try:
        samples = float(row.get('samples') or 0)
        conf = float(row.get('confidence_pct') or 0)
        up = float(row.get('up_pct') or 0)
        down = float(row.get('down_pct') or 0)
        return samples >= MIN_TRUST_SAMPLES and conf >= MIN_TRUST_CONFIDENCE and abs(up-down) >= MIN_TRUST_EDGE
    except Exception:
        return False


def _marketday_lifecycle(ticker, market_date):
    """Persist the active market date and report a one-way day transition.

    Failure is safe: live evidence is still date-filtered by _live_direction.
    """
    key = f'timemap_active_market_date_{str(ticker).upper()}'
    try:
        import app
        previous = str(app.state_get(key) or '')
        changed = bool(previous and previous != str(market_date))
        if previous != str(market_date):
            app.state_set(key, str(market_date))
        return 'NEW_MARKET_DAY' if changed else 'CURRENT_MARKET_DAY'
    except Exception:
        return 'CURRENT_MARKET_DAY'


def reconcile_time_map_live(ticker, time_map, intraday, session):
    if time_map is None or time_map.empty:
        return time_map
    out = time_map.copy()
    out['historical_status'] = out.get('status', 'NEUTRAL')
    out['live_confirmation'] = 'N/A'
    out['live_direction'] = 'UNKNOWN'
    out['live_fresh'] = False
    out['live_age_min'] = np.nan
    out['live_price'] = np.nan
    out['current_session_bars'] = 0
    out['trusted'] = False

    session = dict(session or {})
    try:
        now = pd.Timestamp(session.get('wib'))
        if now.tzinfo is None:
            now = now.tz_localize('Asia/Jakarta')
        else:
            now = now.tz_convert('Asia/Jakarta')
    except Exception:
        now = pd.Timestamp.now(tz='Asia/Jakarta')
    market_date = str(session.get('market_date') or now.date())
    lifecycle = _marketday_lifecycle(ticker, market_date)
    out['market_date'] = market_date
    out['lifecycle'] = lifecycle
    predictive = out['session'].isin(['Sesi I', 'Sesi II']) if 'session' in out.columns else pd.Series(True, index=out.index)

    if not bool(session.get('live')):
        out.loc[predictive, 'live_confirmation'] = 'MARKET NOT LIVE'
        out.loc[predictive & out['status'].isin(['ACTIVE', 'DEVELOPING']), 'status'] = 'WAIT'
        return _decision_first(out)

    live = _live_direction(intraday, market_date, now)
    out['live_direction'] = str(live.get('direction') or 'UNKNOWN')
    out['live_fresh'] = bool(live.get('fresh'))
    out['live_age_min'] = live.get('age_minutes', np.nan)
    out['live_price'] = live.get('last_price', np.nan)
    out['current_session_bars'] = int(live.get('session_bars') or 0)

    if not live.get('usable'):
        out.loc[predictive & out['status'].isin(['ACTIVE', 'DEVELOPING']), 'status'] = 'WAIT'
        reason = str(live.get('reason') or 'NO_LIVE_DATA')
        if reason == 'NEW_MARKET_DAY_NO_BARS':
            out.loc[predictive, 'live_confirmation'] = 'NEW DAY • WAIT LIVE DATA'
        else:
            out.loc[predictive, 'live_confirmation'] = 'NO LIVE CONFIRMATION'
        age = live.get('age_minutes', np.nan)
        age_text = f' age={float(age):.0f}m' if pd.notna(age) else ''
        if 'note' in out.columns:
            out.loc[predictive, 'note'] = out.loc[predictive, 'note'].astype(str) + f' Live blocked: {reason}{age_text}.'
        return _decision_first(out)

    now_min = int(now.hour) * 60 + int(now.minute)
    current_floor = (now_min // 30) * 30
    direction = str(live.get('direction') or 'MIXED')
    for idx, row in out.iterrows():
        if str(row.get('session')) not in {'Sesi I', 'Sesi II'}:
            continue
        slot = _minutes(row.get('time'))
        if slot is None:
            continue
        if slot < current_floor:
            out.at[idx, 'live_confirmation'] = 'HISTORICAL'
            continue
        hist = str(row.get('historical_status') or row.get('status') or 'NEUTRAL').upper()
        hist_trusted = _historical_trust(row)
        if direction == 'DOWN':
            out.at[idx, 'live_confirmation'] = 'DIVERGENCE' if hist in {'ACTIVE','DEVELOPING'} else 'MATCH' if hist == 'WAIT' else 'MIXED'
            if hist in {'ACTIVE', 'DEVELOPING'}:
                out.at[idx, 'status'] = 'WAIT'
            out.at[idx, 'trusted'] = bool(hist == 'WAIT' and hist_trusted)
        elif direction == 'UP':
            out.at[idx, 'live_confirmation'] = 'MATCH' if hist in {'ACTIVE','DEVELOPING'} else 'DIVERGENCE' if hist == 'WAIT' else 'MIXED'
            if hist == 'WAIT':
                out.at[idx, 'status'] = 'NEUTRAL'
            out.at[idx, 'trusted'] = bool(hist in {'ACTIVE','DEVELOPING'} and hist_trusted)
        else:
            out.at[idx, 'live_confirmation'] = 'MIXED'
            if hist in {'ACTIVE', 'DEVELOPING'}:
                out.at[idx, 'status'] = 'NEUTRAL'
            out.at[idx, 'trusted'] = False

    ret_session = float(live.get('ret_day') or 0.0) * 100.0
    ret_last = float(live.get('ret_last') or 0.0) * 100.0
    age = float(live.get('age_minutes') or 0.0)
    suffix = f' Live {direction}: session {ret_session:+.2f}%, last bar {ret_last:+.2f}%, age {age:.0f}m.'
    if 'note' in out.columns:
        mask = out['live_confirmation'].isin(['MATCH', 'MIXED', 'DIVERGENCE', 'NO LIVE CONFIRMATION'])
        out.loc[mask, 'note'] = out.loc[mask, 'note'].astype(str) + suffix
    return _decision_first(out)
