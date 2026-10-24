"""Bounded, ticker-scoped Time Map ingestion; no app import or universe scan."""
from collections import OrderedDict
from threading import RLock
import time
import re

import numpy as np
import pandas as pd
import requests

_CACHE = OrderedDict()
_LOCK = RLock()
_REFRESH_LOCKS = [RLock() for _ in range(16)]
TTL_SECONDS = 90
MAX_CACHE = 64
COLUMNS = ['ts', 'open', 'high', 'low', 'close', 'volume']


def canonical_ticker(value):
    value = str(value or '').upper().strip().removesuffix('.JK')
    if not re.fullmatch(r'[A-Z0-9]{2,6}', value):
        raise ValueError('INVALID TICKER')
    return value


def clean(frame, ticker, now=None):
    """Normalize timestamps, reject a mismatched symbol and invalid OHLC rows."""
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now).tz_convert('UTC')
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    ticker = canonical_ticker(ticker)
    if frame.attrs.get('ticker') and canonical_ticker(frame.attrs['ticker']) != ticker:
        raise ValueError('TICKER MISMATCH')
    if 'ticker' in frame and set(frame.ticker.astype(str).str.upper().str.replace('.JK', '', regex=False)) != {ticker}:
        raise ValueError('TICKER MISMATCH')
    out = frame.reindex(columns=COLUMNS).copy()
    out['ts'] = pd.to_datetime(out.ts, utc=True, errors='coerce')
    for col in COLUMNS[1:]:
        out[col] = pd.to_numeric(out[col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=['ts', 'open', 'high', 'low', 'close'])
    valid = (out.ts <= now) & (out[['open','high','low','close']] > 0).all(axis=1)
    valid &= (out.high >= out[['open','close','low']].max(axis=1)) & (out.low <= out[['open','close','high']].min(axis=1))
    valid &= out.volume.isna() | (out.volume >= 0)
    return out[valid].drop_duplicates('ts',keep='last').sort_values('ts').reset_index(drop=True)


def _download(ticker, days, timeout, now):
    errors = []
    # 59d stays inside the provider's 60-day intraday retention boundary.
    params = {'period1':int(now.timestamp())-min(59, max(2,days))*86400,
              'period2':int(now.timestamp()),'interval':'30m','events':'history'}
    for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
        try:
            response = requests.get(f'https://{host}/v8/finance/chart/{ticker}.JK',params=params,
                                    headers={'User-Agent':'Mozilla/5.0'}, timeout=(3,timeout))
            response.raise_for_status()
            chart = response.json().get('chart') or {}
            results = chart.get('result') or []
            if not results:
                raise ValueError('EMPTY CHART')
            raw = results[0]
            if str((raw.get('meta') or {}).get('symbol','')).upper() != ticker+'.JK':
                raise ValueError('PROVIDER SYMBOL MISMATCH')
            ts = raw.get('timestamp') or []
            quote = ((raw.get('indicators') or {}).get('quote') or [{}])[0]
            frame = clean(pd.DataFrame({'ts':pd.to_datetime(ts,unit='s',utc=True),
                        **{c:quote.get(c,[None]*len(ts)) for c in COLUMNS[1:]}}),ticker,now)
            if frame.empty:
                raise ValueError('EMPTY VALID BARS')
            return frame,host,errors
        except Exception as exc:
            code = getattr(getattr(exc,'response',None),'status_code',None)
            errors.append(f'{host}: {"HTTP "+str(code) if code else type(exc).__name__+": "+str(exc)[:65]}')
    return pd.DataFrame(columns=COLUMNS),'UNAVAILABLE',errors


def load(ticker, days=59, timeout=6, now=None):
    """Shared bounded cache by ticker/date/range. Empty responses expire too."""
    ticker = canonical_ticker(ticker)
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now).tz_convert('UTC')
    days = min(59,max(2,int(days)))
    key = (ticker,now.tz_convert('Asia/Jakarta').date().isoformat(),days)
    started = time.perf_counter()
    # Same-key requests coalesce; unrelated symbols can fetch concurrently.
    with _REFRESH_LOCKS[hash(key) % len(_REFRESH_LOCKS)]:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and time.monotonic()-cached[0] < TTL_SECONDS:
            out = cached[1].copy(deep=True)
            out.attrs = dict(cached[1].attrs,cache_hit=True,elapsed_ms=round((time.perf_counter()-started)*1000,2))
            return out
        frame,source,errors = _download(ticker,days,timeout,now)
        if frame.empty and days > 5 and not (cached and not cached[1].empty):
            # Recover today's tape even when the long history request fails.
            frame,source,recent_errors = _download(ticker,5,timeout,now)
            errors += recent_errors
            if not frame.empty:
                source += ' • 5d recovery'
        # A failed refresh may retain dated bars; their timestamps are never advanced.
        fallback = False
        original_fetch_time = now.isoformat()
        if frame.empty and cached and not cached[1].empty:
            frame = cached[1].copy(deep=True)
            source = 'LAST GOOD CACHE'
            fallback = True
            # Never stamp an old observation with the time of a failed refresh.
            original_fetch_time = frame.attrs.get('fetched_at') or None
        frame.attrs = dict(ticker=ticker,source=source,errors=errors,cache_hit=False,
                           fallback=fallback, fetched_at=original_fetch_time,
                           refresh_attempt_at=now.isoformat(),
                           latest_bar=str(frame.ts.max()) if not frame.empty else None,
                           elapsed_ms=round((time.perf_counter()-started)*1000,2))
        with _LOCK:
            _CACHE[key]=(time.monotonic(),frame.copy(deep=True))
            _CACHE.move_to_end(key)
            while len(_CACHE)>MAX_CACHE:
                _CACHE.popitem(last=False)
        return frame
