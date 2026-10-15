"""V1.8.43 persistent live Top-5 / ENTRY READY history and guarded FCM.

Full-universe snapshots may still run outside market hours, but live baselines are
silently synchronized and actionable events/FCM are only produced in an active
IDX regular-market session.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from market_session import session_status

WIB = ZoneInfo('Asia/Jakarta')


def _utc():
    return datetime.now(timezone.utc)


def _connect(db_path):
    con = sqlite3.connect(db_path, timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    con.execute('PRAGMA journal_mode=WAL')
    return con


def ensure_tables(db_path):
    with _connect(db_path) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS top5_live_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            updated_at TEXT NOT NULL, tickers_json TEXT NOT NULL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS top5_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_at_utc TEXT NOT NULL, event_at_wib TEXT NOT NULL,
            event_type TEXT NOT NULL, ticker TEXT NOT NULL,
            old_rank INTEGER, new_rank INTEGER,
            replaced_ticker TEXT, replacing_ticker TEXT,
            old_top5_json TEXT NOT NULL, new_top5_json TEXT NOT NULL,
            reason TEXT, notification_status TEXT NOT NULL DEFAULT 'PENDING',
            notification_attempts INTEGER NOT NULL DEFAULT 0,
            notification_last_attempt TEXT, notification_error TEXT,
            notification_sent_at TEXT)''')
        con.execute('''CREATE TABLE IF NOT EXISTS live_action_state (
            ticker TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            state TEXT,
            entry_status TEXT,
            is_entry_ready INTEGER NOT NULL DEFAULT 0)''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_top5_timeline_time ON top5_timeline(event_at_utc DESC)')


def _ranked(dynamic):
    if dynamic is None or dynamic.empty or 'ticker' not in dynamic.columns:
        return []
    df = dynamic.copy()
    if 'opportunity_rank' in df.columns:
        df['_rank'] = __import__('pandas').to_numeric(df['opportunity_rank'], errors='coerce')
        df = df[df['_rank'].notna() & (df['_rank'] >= 1)].sort_values('_rank', kind='stable')
    elif 'rank' in df.columns:
        df['_rank'] = __import__('pandas').to_numeric(df['rank'], errors='coerce')
        df = df[df['_rank'].notna() & (df['_rank'] >= 1)].sort_values('_rank', kind='stable')
    out = []
    for value in df['ticker']:
        ticker = str(value or '').strip().upper()
        if ticker and ticker not in out and ticker not in {'NAN', 'NONE'}:
            out.append(ticker)
        if len(out) == 5:
            break
    return out


def _row_value(row, name, default=None):
    try:
        value = row.get(name, default)
        if value is None or str(value).lower() == 'nan':
            return default
        return value
    except Exception:
        return default


def _sync_action_state(con, dynamic, utc_iso, create_events, old_top5, new_top5, wib_iso):
    """Persist state and create one event only when a ticker transitions INTO ready."""
    if dynamic is None or dynamic.empty or 'ticker' not in dynamic.columns:
        return 0
    events = 0
    for _, row in dynamic.iterrows():
        ticker = str(_row_value(row, 'ticker', '') or '').strip().upper()
        if not ticker:
            continue
        state = str(_row_value(row, 'state', '') or '').strip().upper()
        entry = str(_row_value(row, 'entry_status', '') or '').strip().upper()
        mode = str(_row_value(row, 'candidate_mode', '') or '').strip().upper()
        ready = int(mode != 'MONITOR_FALLBACK' and state == 'ENTRY READY' and entry == 'ACTIVE')
        previous = con.execute('SELECT is_entry_ready FROM live_action_state WHERE ticker=?', (ticker,)).fetchone()
        was_ready = int(previous[0]) if previous else 0

        if create_events and ready and not was_ready:
            p_up = _row_value(row, 'p_up', None)
            rr = _row_value(row, 'risk_reward', _row_value(row, 'R/R', None))
            risk = str(_row_value(row, 'risk', '') or '').upper()
            try:
                pup_text = f'{float(p_up)*100:.1f}%' if float(p_up) <= 1 else f'{float(p_up):.1f}%'
            except Exception:
                pup_text = 'N/A'
            try:
                rr_text = f'{float(rr):.2f}'
            except Exception:
                rr_text = 'N/A'
            reason = f'ENTRY READY aktif. P(UP) {pup_text} | R/R {rr_text} | Risk {risk or "N/A"}'
            rank = new_top5.index(ticker) + 1 if ticker in new_top5 else None
            con.execute('''INSERT INTO top5_timeline
                (event_at_utc,event_at_wib,event_type,ticker,old_rank,new_rank,
                 replaced_ticker,replacing_ticker,old_top5_json,new_top5_json,reason,notification_status)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (utc_iso,wib_iso,'ENTRY_READY',ticker,rank,rank,'','',
                 json.dumps(old_top5),json.dumps(new_top5),reason,'PENDING'))
            events += 1

        con.execute('''INSERT INTO live_action_state(ticker,updated_at,state,entry_status,is_entry_ready)
            VALUES(?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET updated_at=excluded.updated_at,state=excluded.state,
            entry_status=excluded.entry_status,is_entry_ready=excluded.is_entry_ready''',
            (ticker,utc_iso,state,entry,ready))
    return events


def record_top5(db_path, dynamic, source_phase='full-universe', config=None):
    """Record live transitions. Outside session only synchronize baselines, never alert."""
    if str(source_phase).lower() != 'full-universe':
        return {'status': 'SKIPPED_SOURCE', 'events': 0}
    new = _ranked(dynamic)
    if not new:
        return {'status': 'SKIPPED_EMPTY', 'events': 0}

    ensure_tables(db_path)
    market = session_status(config)
    live = bool(market.get('live'))
    now = _utc()
    utc_iso = now.isoformat()
    wib_iso = now.astimezone(WIB).isoformat()

    with _connect(db_path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()
        if row is None:
            con.execute('INSERT INTO top5_live_state(singleton,updated_at,tickers_json) VALUES(1,?,?)',
                        (utc_iso, json.dumps(new)))
            _sync_action_state(con, dynamic, utc_iso, False, new, new, wib_iso)
            return {'status': 'BASELINE_CREATED', 'events': 0, 'top5': new, 'market': market.get('status')}
        try:
            old = json.loads(row[0])
        except (ValueError, TypeError):
            old = []
        if not isinstance(old, list):
            old = []

        # Critical guard: after close/lunch/pre-market/weekends, keep the baseline fresh
        # so the next live cycle does not manufacture a stale overnight Top-5 change.
        if not live:
            con.execute('UPDATE top5_live_state SET updated_at=?,tickers_json=? WHERE singleton=1',
                        (utc_iso, json.dumps(new)))
            _sync_action_state(con, dynamic, utc_iso, False, old, new, wib_iso)
            return {'status': 'SYNC_NON_LIVE', 'events': 0, 'top5': new, 'market': market.get('status')}

        events = []
        entered = [t for t in new if t not in old]
        exited = [t for t in old if t not in new]
        actionable_tickers = set()
        if dynamic is not None and not dynamic.empty:
            for _, item in dynamic.iterrows():
                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':
                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())
        actionable_tickers = set()
        if dynamic is not None and not dynamic.empty:
            for _, item in dynamic.iterrows():
                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':
                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())
        actionable_tickers = set()
        if dynamic is not None and not dynamic.empty:
            for _, item in dynamic.iterrows():
                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':
                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())
        actionable_tickers = set()
        if dynamic is not None and not dynamic.empty:
            for _, item in dynamic.iterrows():
                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':
                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())
        if old != new:
            for i, ticker in enumerate(entered):
                if ticker not in actionable_tickers:
                    continue
                replaced = exited[i] if i < len(exited) else ''
                events.append(('ENTRY', ticker, old.index(ticker)+1 if ticker in old else None,
                               new.index(ticker)+1, replaced, '',
                               f'{ticker} masuk Top 5 peringkat #{new.index(ticker)+1}' +
                               (f', menggantikan {replaced}' if replaced else '')))
            for i, ticker in enumerate(exited):
                replacement = entered[i] if i < len(entered) else ''
                events.append(('EXIT', ticker, old.index(ticker)+1, None, '', replacement,
                               f'{ticker} keluar Top 5' + (f', digantikan {replacement}' if replacement else '')))
            for ticker in new:
                if ticker in old and old.index(ticker) != new.index(ticker):
                    events.append(('RANK_CHANGE', ticker, old.index(ticker)+1, new.index(ticker)+1,
                                   '', '', f'{ticker} peringkat #{old.index(ticker)+1} → #{new.index(ticker)+1}'))
            for kind, ticker, old_rank, new_rank, replaced, replacing, reason in events:
                status = 'PENDING' if kind == 'ENTRY' else 'NOT_REQUIRED'
                con.execute('''INSERT INTO top5_timeline
                    (event_at_utc,event_at_wib,event_type,ticker,old_rank,new_rank,
                     replaced_ticker,replacing_ticker,old_top5_json,new_top5_json,reason,notification_status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (utc_iso,wib_iso,kind,ticker,old_rank,new_rank,replaced,replacing,
                     json.dumps(old),json.dumps(new),reason,status))

        ready_events = _sync_action_state(con, dynamic, utc_iso, True, old, new, wib_iso)
        con.execute('UPDATE top5_live_state SET updated_at=?,tickers_json=? WHERE singleton=1',
                    (utc_iso,json.dumps(new)))

    total = len(events) + ready_events
    status = 'UNCHANGED' if total == 0 else 'CHANGED'
    return {'status': status, 'events': total, 'top5_events': len(events),
            'entry_ready_events': ready_events, 'entered': entered, 'exited': exited,
            'top5': new, 'market': market.get('status')}


def dispatch_fcm(db_path, config=None, limit=5):
    """Retry pending live alerts only while IDX regular market is actively trading."""
    ensure_tables(db_path)
    market = session_status(config)
    if not market.get('live'):
        return {'status': 'BLOCKED_NON_LIVE', 'market': market.get('status'), 'sent': 0, 'failed': 0}
    try:
        from fcm_adapter import send_fcm, fcm_status
        readiness = fcm_status(config)
    except Exception as exc:
        return {'status': 'ADAPTER_ERROR', 'detail': str(exc)[:200], 'sent': 0, 'failed': 0}
    if readiness.get('mode') != 'FCM_SEND_READY':
        return {'status': readiness.get('mode', 'NOT_READY'), 'sent': 0, 'failed': 0}

    now = _utc()
    with _connect(db_path) as con:
        pending = con.execute('''SELECT id,event_type,ticker,replaced_ticker,reason,notification_attempts,
                               notification_last_attempt FROM top5_timeline
                               WHERE notification_status IN ('PENDING','RETRY')
                               AND notification_attempts < 3
                               ORDER BY id ASC LIMIT ?''', (int(limit),)).fetchall()
    sent = failed = 0
    for event_id, event_type, ticker, replaced, reason, attempts, last_attempt in pending:
        if last_attempt:
            try:
                last = datetime.fromisoformat(last_attempt)
                if now - last.astimezone(timezone.utc) < timedelta(minutes=15):
                    continue
            except (ValueError, TypeError):
                pass
        with _connect(db_path) as con:
            cur = con.execute('''UPDATE top5_timeline SET notification_status='SENDING',
                notification_attempts=notification_attempts+1,notification_last_attempt=?
                WHERE id=? AND notification_status IN ('PENDING','RETRY')''', (now.isoformat(),event_id))
            if cur.rowcount != 1:
                continue

        if event_type == 'ENTRY_READY':
            title = f'ENTRY READY — {ticker}'
            detail = reason + ' | Buka aplikasi untuk Entry Range, Stop Loss, Target.'
            notification_id = f'ENTRYREADY-{event_id}'
        else:
            title = f'Masuk Top 5: {ticker}' + (f' menggantikan {replaced}' if replaced else '')
            detail = reason
            notification_id = f'TOP5-{event_id}'
        notification = {'ticker': ticker, 'severity': 'HIGH', 'title': title,
                        'detail': detail, 'notification_id': notification_id}
        try:
            result = send_fcm(notification, config)
            ok = bool(result.get('ok'))
            err = '' if ok else str(result.get('error') or result.get('response') or result.get('mode'))[:400]
        except Exception as exc:
            ok, err = False, str(exc)[:400]
        with _connect(db_path) as con:
            con.execute('''UPDATE top5_timeline SET notification_status=?,
                notification_error=?,notification_sent_at=? WHERE id=?''',
                ('SENT' if ok else ('FAILED' if attempts+1 >= 3 else 'RETRY'),
                 err, _utc().isoformat() if ok else None, event_id))
        sent += int(ok)
        failed += int(not ok)
    return {'status': 'DONE', 'sent': sent, 'failed': failed, 'market': market.get('status')}


def recent_events(db_path, limit=100):
    ensure_tables(db_path)
    with _connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('''SELECT event_at_wib,event_type,ticker,old_rank,new_rank,
             replaced_ticker,replacing_ticker,reason,notification_status
             FROM top5_timeline ORDER BY id DESC LIMIT ?''', (int(limit),)).fetchall()
        return [dict(row) for row in rows]
