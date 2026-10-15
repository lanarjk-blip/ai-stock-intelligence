from pathlib import Path

# R34: wire Smart Alerts to the authoritative worker as in-app history only.
p = Path('smart_alerts.py')
s = p.read_text(encoding='utf-8')

s = s.replace(
    'from typing import Any, Dict, List\n',
    'from typing import Any, Dict, List\nimport json\nimport sqlite3\nfrom datetime import datetime, timezone\n\nimport pandas as pd\n',
    1,
)

insert = s.index('\ndef evaluate_transition(')
helper = r'''

def _b(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {'1','true','yes','on'}


def _live_actionable(snapshot: Dict[str, Any]) -> bool:
    """An entry alert is actionable only with current-session evidence."""
    mode = _s(snapshot.get('candidate_mode'), 'STRICT').upper()
    state = _s(snapshot.get('state'), '').upper()
    entry = _s(snapshot.get('entry_status'), 'NEUTRAL').upper()
    return (
        mode != 'MONITOR_FALLBACK'
        and _b(snapshot.get('live_required'))
        and _b(snapshot.get('intraday_fresh'))
        and state == 'ENTRY READY'
        and entry == 'ACTIVE'
    )
'''
s = s[:insert] + helper + s[insert:]

old = "    old_opp, cur_opp = _f(old.get('opportunity_score')), _f(cur.get('opportunity_score'))\n"
new = old + "    cur_actionable = _live_actionable(cur)\n"
if old not in s:
    raise SystemExit('smart alert opportunity anchor missing')
s = s.replace(old, new, 1)

old = "        sev = 'HIGH' if cur_entry in ('ACTIVE', 'INVALIDATED') or (cur_opp is not None and cur_opp >= outside_score) else 'MEDIUM'\n"
new = "        sev = 'HIGH' if cur_actionable or cur_entry == 'INVALIDATED' or (cur_opp is not None and cur_opp >= outside_score) else 'MEDIUM'\n"
if old not in s:
    raise SystemExit('top5 severity anchor missing')
s = s.replace(old, new, 1)

old = "        entry_breakout = cur_entry == 'ACTIVE' and old_entry != 'ACTIVE' and (cur_opp is None or cur_opp >= outside_score - 3)\n"
new = "        entry_breakout = cur_actionable and old_entry != 'ACTIVE' and (cur_opp is None or cur_opp >= outside_score - 3)\n"
if old not in s:
    raise SystemExit('outside top5 entry anchor missing')
s = s.replace(old, new, 1)

old = """    if old_entry != cur_entry:
        sev = 'HIGH' if cur_entry in ('ACTIVE', 'INVALIDATED') or old_entry == 'ACTIVE' else 'MEDIUM'
        add('ENTRY_WINDOW', 'Entry Window berubah', f'{old_entry} → {cur_entry}', old_entry, cur_entry, sev)

    if old_dec != cur_dec:
        sev = 'HIGH' if cur_dec in ('BUY', 'SELL/AVOID') or old_dec in ('BUY', 'SELL/AVOID') else 'MEDIUM'
"""
new = """    if old_entry != cur_entry:
        # New ACTIVE is actionable only when live state + freshness agree.
        if cur_entry != 'ACTIVE' or cur_actionable:
            sev = 'HIGH' if cur_entry == 'INVALIDATED' or old_entry == 'ACTIVE' or cur_actionable else 'MEDIUM'
            add('ENTRY_WINDOW', 'Entry Window berubah', f'{old_entry} → {cur_entry}', old_entry, cur_entry, sev)

    if old_dec != cur_dec:
        sev = 'HIGH' if cur_dec in ('SELL/AVOID','SELL','AVOID') or old_dec in ('SELL/AVOID','SELL','AVOID') or (cur_dec in ('BUY','STRONG_BUY') and cur_actionable) else 'MEDIUM'
"""
if old not in s:
    raise SystemExit('entry/decision alert anchor missing')
s = s.replace(old, new, 1)

insert = s.index('\ndef simulator_cases()')
persist = r'''

def ensure_inapp_storage(db_path: str) -> None:
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS smart_alert_state (
            ticker TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            ticker TEXT,
            company TEXT,
            severity TEXT,
            alert_type TEXT,
            title TEXT,
            detail TEXT,
            old_value TEXT,
            new_value TEXT,
            source_phase TEXT
        )""")
        existing = {str(r[1]) for r in con.execute('PRAGMA table_info(alert_history)').fetchall()}
        if 'source_phase' not in existing:
            con.execute('ALTER TABLE alert_history ADD COLUMN source_phase TEXT')
        con.commit()
    finally:
        con.close()


def _clean(v):
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if hasattr(v, 'item'):
        try:
            v = v.item()
        except Exception:
            pass
    return v


def _snapshot_from_rows(row: Dict[str, Any], hold: Dict[str, Any], *, live_required: bool) -> Dict[str, Any]:
    return {
        'ticker': _s(row.get('ticker')).upper().replace('.JK',''),
        'company': _s(row.get('company'), _s(row.get('ticker'))),
        'decision': _s(row.get('decision'), 'WATCH').upper(),
        'risk': _s(row.get('risk'), 'UNKNOWN').upper(),
        'entry_status': _s(row.get('entry_status'), 'NEUTRAL').upper(),
        'state': _s(row.get('state'), 'MONITOR').upper(),
        'candidate_mode': _s(row.get('candidate_mode'), 'STRICT').upper(),
        'intraday_fresh': _b(row.get('intraday_fresh')),
        'price_source': _s(row.get('price_source'), '').upper(),
        'live_required': bool(live_required),
        'hold_state': _s(hold.get('state'), 'UNKNOWN').upper(),
        'overnight_state': _s(hold.get('overnight_state'), 'UNKNOWN').upper(),
        'next_session_bias': _s(hold.get('next_session_bias'), 'UNKNOWN').upper(),
        'opportunity_rank': _i(row.get('rank', row.get('opportunity_rank'))),
        'relative_rank': _i(row.get('relative_rank')),
        'ai_score': _f(row.get('ai_score')),
        'opportunity_score': _f(row.get('opportunity_score')),
    }


def sync_inapp_alerts(db_path: str, dynamic_df, hold_df=None, *, config=None, phase='full-universe', live_required=False) -> Dict[str, Any]:
    """Compare authoritative snapshots and persist in-app alerts only."""
    ensure_inapp_storage(db_path)
    if dynamic_df is None or getattr(dynamic_df, 'empty', True):
        return {'status':'NO_SNAPSHOT','alerts':0,'states':0}

    cfg = config or {}
    limit = max(10, int(cfg.get('smart_alert_history_limit', 30)))
    top_n = max(1, int(cfg.get('smart_alert_top_n', TOP_N)))
    delta = float(cfg.get('smart_alert_change_threshold', DEFAULT_SCORE_DELTA))
    outside = float(cfg.get('smart_alert_outside_top5_score', DEFAULT_OUTSIDE_SCORE))

    holds = {}
    if hold_df is not None and not getattr(hold_df, 'empty', True) and 'ticker' in hold_df.columns:
        for _, r in hold_df.iterrows():
            d = {str(k): _clean(v) for k, v in r.to_dict().items()}
            holds[_s(d.get('ticker')).upper().replace('.JK','')] = d

    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        prior = {}
        for r in con.execute('SELECT ticker,snapshot_json FROM smart_alert_state').fetchall():
            try:
                prior[str(r['ticker'])] = json.loads(r['snapshot_json'])
            except Exception:
                prior[str(r['ticker'])] = {}

        current = {}
        alerts = []
        for _, r in dynamic_df.iterrows():
            row = {str(k): _clean(v) for k, v in r.to_dict().items()}
            ticker = _s(row.get('ticker')).upper().replace('.JK','')
            if not ticker:
                continue
            snap = _snapshot_from_rows(row, holds.get(ticker, {}), live_required=bool(live_required))
            current[ticker] = snap
            old = prior.get(ticker)
            if old:
                alerts.extend(evaluate_transition(old, snap, top_n=top_n, score_delta=delta, outside_score=outside))

        # Preserve absent tickers as non-actionable state so a later re-entry is detectable.
        state_payload = dict(current)
        for ticker, old in prior.items():
            if ticker in current:
                continue
            absent = dict(old)
            absent['opportunity_rank'] = None
            absent['entry_status'] = 'NEUTRAL'
            absent['state'] = 'MONITOR'
            absent['intraday_fresh'] = False
            state_payload[ticker] = absent

        insert_sql = (
            'INSERT INTO alert_history('
            'generated_at,ticker,company,severity,alert_type,title,detail,old_value,new_value,source_phase'
            ') VALUES(?,?,?,?,?,?,?,?,?,?)'
        )
        for a in alerts:
            con.execute(
                insert_sql,
                (now, _s(a.get('ticker')), _s(a.get('company')), _s(a.get('severity')),
                 _s(a.get('alert_type')), _s(a.get('title')), _s(a.get('detail')),
                 _s(a.get('old_value')), _s(a.get('new_value')), _s(phase))
            )
        for ticker, snap in state_payload.items():
            con.execute(
                'INSERT OR REPLACE INTO smart_alert_state(ticker,snapshot_json,updated_at) VALUES(?,?,?)',
                (ticker, json.dumps(snap, ensure_ascii=False, sort_keys=True), now)
            )
        con.execute(
            'DELETE FROM alert_history WHERE id NOT IN (SELECT id FROM alert_history ORDER BY id DESC LIMIT ?)',
            (limit,)
        )
        con.commit()
        return {'status':'SEEDED' if not prior else 'UPDATED','alerts':len(alerts),'states':len(state_payload)}
    finally:
        con.close()
'''
s = s[:insert] + persist + s[insert:]
p.write_text(s, encoding='utf-8')

# Wire the in-app alert feed after Hold has been refreshed.
w = Path('worker_base.py')
ws = w.read_text(encoding='utf-8')
if 'import smart_alerts\n' not in ws:
    ws = ws.replace('import accuracy_guardrails\n', 'import accuracy_guardrails\nimport smart_alerts\n', 1)

hold_anchor = '''    # Hold Horizon intelligence is refreshed only from the authoritative full-universe
    # result and receives the actual IDX session phase, not the scan-source label.
    if str(source).lower() == "full-universe":
        try:
'''
hold_replacement = '''    # Hold Horizon intelligence is refreshed only from the authoritative full-universe
    # result and receives the actual IDX session phase, not the scan-source label.
    hold = pd.DataFrame()
    if str(source).lower() == "full-universe":
        try:
'''
if hold_anchor not in ws:
    raise SystemExit('worker hold anchor missing')
ws = ws.replace(hold_anchor, hold_replacement, 1)

alert_anchor = '    # Strict next-session Closing / Overnight Intelligence is immutable during\n'
if alert_anchor not in ws:
    raise SystemExit('worker alert insert anchor missing')
alert_block = '''    # Smart Alert is an in-app audit/feed only. No WhatsApp/FCM delivery is enabled here.
    if str(source).lower() == "full-universe" and dynamic_ok:
        try:
            alert_session = market_session.session_status(CFG)
            smart = smart_alerts.sync_inapp_alerts(
                app.DB, dynamic, hold, config=CFG, phase=source,
                live_required=bool(alert_session.get("live")),
            )
            app.state_set("smart_alert_status", smart.get("status", "UNKNOWN"))
            app.state_set("smart_alert_at", datetime.now(timezone.utc).isoformat())
            app.state_set("smart_alert_last_count", smart.get("alerts", 0))
            log("SMART_ALERT", smart.get("status", "UNKNOWN"),
                f"alerts={smart.get('alerts',0)} states={smart.get('states',0)}")
        except Exception:
            app.state_set("smart_alert_status", "ERROR")
            app.state_set("smart_alert_error", traceback.format_exc()[-900:])
            log("SMART_ALERT", "ERROR", traceback.format_exc()[-1800:])

'''
ws = ws.replace(alert_anchor, alert_block + alert_anchor, 1)
w.write_text(ws, encoding='utf-8')
print('R34 IN-APP SMART ALERT WIRING APPLIED')
