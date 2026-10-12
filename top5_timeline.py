"""V1.8.42 persistent live Top-5 transition history and FCM dispatcher.

Only full-universe snapshots are compared. Initial snapshot establishes baseline.
No secrets are stored; fcm_adapter reads existing environment/config.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

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


def record_top5(db_path, dynamic, source_phase='full-universe'):
    """Save immutable events on membership/rank changes. Return new event count."""
    if str(source_phase).lower() != 'full-universe':
        return {'status': 'SKIPPED_SOURCE', 'events': 0}
    new = _ranked(dynamic)
    if not new:
        return {'status': 'SKIPPED_EMPTY', 'events': 0}
    ensure_tables(db_path)
    now = _utc()
    utc_iso = now.isoformat()
    wib_iso = now.astimezone(WIB).isoformat()
    with _connect(db_path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()
        if row is None:
            con.execute('INSERT INTO top5_live_state(singleton,updated_at,tickers_json) VALUES(1,?,?)',
                        (utc_iso, json.dumps(new)))
            return {'status': 'BASELINE_CREATED', 'events': 0, 'top5': new}
        try:
            old = json.loads(row[0])
        except (ValueError, TypeError):
            return {'status': 'INVALID_BASELINE', 'events': 0}
        if not isinstance(old, list):
            return {'status': 'INVALID_BASELINE', 'events': 0}
        if old == new:
            con.execute('UPDATE top5_live_state SET updated_at=? WHERE singleton=1', (utc_iso,))
            return {'status': 'UNCHANGED', 'events': 0, 'top5': new}
        entered = [t for t in new if t not in old]
        exited = [t for t in old if t not in new]
        events = []
        for i, ticker in enumerate(entered):
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
            # EXIT and rank movements are recorded, but only ENTRY is pushed to avoid duplicate alerts.
            status = 'PENDING' if kind == 'ENTRY' else 'NOT_REQUIRED'
            con.execute('''INSERT INTO top5_timeline
                (event_at_utc,event_at_wib,event_type,ticker,old_rank,new_rank,
                 replaced_ticker,replacing_ticker,old_top5_json,new_top5_json,reason,notification_status)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (utc_iso,wib_iso,kind,ticker,old_rank,new_rank,replaced,replacing,
                  json.dumps(old),json.dumps(new),reason,status))
        con.execute('UPDATE top5_live_state SET updated_at=?,tickers_json=? WHERE singleton=1',
                    (utc_iso,json.dumps(new)))
    return {'status': 'CHANGED', 'events': len(events), 'entered': entered, 'exited': exited, 'top5': new}


def dispatch_fcm(db_path, config=None, limit=5):
    """Retry pending entry alerts. Never mark delivered without FCM API success."""
    ensure_tables(db_path)
    try:
        from fcm_adapter import send_fcm, fcm_status
        readiness = fcm_status(config)
    except Exception as exc:
        return {'status': 'ADAPTER_ERROR', 'detail': str(exc)[:200], 'sent': 0}
    if readiness.get('mode') != 'FCM_SEND_READY':
        return {'status': readiness.get('mode', 'NOT_READY'), 'sent': 0}
    now = _utc()
    with _connect(db_path) as con:
        pending = con.execute('''SELECT id,ticker,replaced_ticker,reason,notification_attempts,
                               notification_last_attempt FROM top5_timeline
                               WHERE notification_status IN ('PENDING','RETRY')
                               AND notification_attempts < 3
                               ORDER BY id ASC LIMIT ?''', (int(limit),)).fetchall()
    sent = failed = 0
    for event_id, ticker, replaced, reason, attempts, last_attempt in pending:
        if last_attempt:
            try:
                last = datetime.fromisoformat(last_attempt)
                if now - last.astimezone(timezone.utc) < timedelta(minutes=15):
                    continue
            except (ValueError, TypeError):
                pass
        # Claim event first so concurrent worker cycles cannot send it twice.
        with _connect(db_path) as con:
            cur = con.execute('''UPDATE top5_timeline SET notification_status='SENDING',
                notification_attempts=notification_attempts+1,notification_last_attempt=?
                WHERE id=? AND notification_status IN ('PENDING','RETRY')''', (now.isoformat(),event_id))
            if cur.rowcount != 1:
                continue
        notification = {
            'ticker': ticker, 'severity': 'HIGH',
            'title': f'Masuk Top 5: {ticker}' + (f' menggantikan {replaced}' if replaced else ''),
            'detail': reason, 'notification_id': f'TOP5-{event_id}'
        }
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
    return {'status': 'DONE', 'sent': sent, 'failed': failed}


def recent_events(db_path, limit=100):
    """UI helper: returns rows with WIB timestamps, including entries/exits/rank changes."""
    ensure_tables(db_path)
    with _connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('''SELECT event_at_wib,event_type,ticker,old_rank,new_rank,
             replaced_ticker,replacing_ticker,reason,notification_status
             FROM top5_timeline ORDER BY id DESC LIMIT ?''', (int(limit),)).fetchall()
        return [dict(row) for row in rows]
