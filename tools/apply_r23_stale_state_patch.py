from pathlib import Path


def patch(path, old, new, label):
    p=Path(path)
    s=p.read_text(encoding='utf-8')
    if old in s:
        s=s.replace(old,new,1)
    elif new not in s:
        raise SystemExit(f'{path}: missing {label}')
    p.write_text(s,encoding='utf-8')


# Dynamic Opportunity: clear stale rows, normalize sell aliases, clean audit duplication.
p=Path('dynamic_engine.py'); s=p.read_text(encoding='utf-8')
line="        app.state_set('opening_confirmation_status','READY')\n"
while line+line in s:
    s=s.replace(line+line,line,1)
p.write_text(s,encoding='utf-8')
patch('dynamic_engine.py',
"def update(res, phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        return pd.DataFrame()\n\n    prev=read_current()\n",
"def _clear_dynamic_current(mode='NO_TRADE'):\n    con=dbcon()\n    try:\n        con.execute('DELETE FROM dynamic_opportunities')\n        con.execute('DELETE FROM dynamic_opportunity_pool')\n        con.commit()\n    finally:\n        con.close()\n    app.state_set('dynamic_opportunity_at',datetime.now(timezone.utc).isoformat())\n    app.state_set('dynamic_opportunity_count',0)\n    app.state_set('dynamic_candidate_mode',str(mode))\n\n\ndef update(res, phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        _clear_dynamic_current('NO_DATA')\n        return pd.DataFrame()\n\n    prev=read_current()\n",
'dynamic stale clear')
patch('dynamic_engine.py',
"    base=res.copy()\n    non_sell=base[~base.decision.isin(['SELL/AVOID'])].copy()\n",
"    base=res.copy()\n    blocked={'SELL','SELL/AVOID','AVOID','STRONG_SELL','EXIT'}\n    decisions=base.decision.astype(str).str.upper().str.strip()\n    non_sell=base[~decisions.isin(blocked)].copy()\n",
'dynamic sell aliases')
patch('dynamic_engine.py',
"        if c.empty:\n            app.state_set('dynamic_candidate_mode','NO_TRADE')\n            app.state_set('dynamic_opportunity_count',0)\n            return pd.DataFrame()\n",
"        if c.empty:\n            _clear_dynamic_current('NO_TRADE')\n            return pd.DataFrame()\n",
'dynamic no-trade clear')

# Top5: clean duplicate guard blocks and make an empty authoritative snapshot clear old state.
p=Path('top5_timeline.py'); s=p.read_text(encoding='utf-8')
block=("        actionable_tickers = set()\n"
       "        if dynamic is not None and not dynamic.empty:\n"
       "            for _, item in dynamic.iterrows():\n"
       "                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':\n"
       "                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())\n")
while block+block in s:
    s=s.replace(block+block,block,1)
p.write_text(s,encoding='utf-8')
patch('top5_timeline.py',
"    new = _ranked(dynamic)\n    if not new:\n        return {'status': 'SKIPPED_EMPTY', 'events': 0}\n\n    ensure_tables(db_path)\n    market = session_status(config)\n",
"    new = _ranked(dynamic)\n    ensure_tables(db_path)\n    market = session_status(config)\n",
'top5 empty early return')
patch('top5_timeline.py',
"    with _connect(db_path) as con:\n        con.execute('BEGIN IMMEDIATE')\n        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()\n        if row is None:\n",
"    with _connect(db_path) as con:\n        con.execute('BEGIN IMMEDIATE')\n        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()\n        if not new:\n            old=[]\n            if row is not None:\n                try:\n                    old=json.loads(row[0]) or []\n                except (ValueError,TypeError):\n                    old=[]\n                con.execute('UPDATE top5_live_state SET updated_at=?,tickers_json=? WHERE singleton=1',(utc_iso,json.dumps([])))\n            else:\n                con.execute('INSERT INTO top5_live_state(singleton,updated_at,tickers_json) VALUES(1,?,?)',(utc_iso,json.dumps([])))\n            con.execute(\"UPDATE live_action_state SET updated_at=?,state='INACTIVE',entry_status='EMPTY',is_entry_ready=0\",(utc_iso,))\n            con.execute(\"UPDATE top5_timeline SET notification_status='CANCELLED_STALE' WHERE notification_status IN ('PENDING','RETRY')\")\n            return {'status':'CLEARED_EMPTY','events':0,'top5':[],'previous_top5':old,'market':market.get('status')}\n        if row is None:\n",
'top5 clear stale baseline')

# Hold Intelligence: zero candidates must remove yesterday's visible rows.
patch('hold_engine.py',
"def update(res,phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty: return pd.DataFrame()\n",
"def _clear_current(status='NO_CANDIDATE'):\n    con=dbcon()\n    try:\n        con.execute('DELETE FROM hold_intelligence')\n        con.commit()\n    finally:\n        con.close()\n    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat())\n    app.state_set('hold_intelligence_count',0)\n    app.state_set('hold_intelligence_status',status)\n\n\ndef update(res,phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        _clear_current('NO_DATA')\n        return pd.DataFrame()\n",
'hold stale clear helper')
p=Path('hold_engine.py'); h=p.read_text(encoding='utf-8')
if "    if c.empty: return pd.DataFrame()\n" in h:
    h=h.replace("    if c.empty: return pd.DataFrame()\n","    if c.empty:\n        _clear_current('NO_CANDIDATE')\n        return pd.DataFrame()\n",1)
if "app.state_set('hold_intelligence_status','READY')" not in h:
    h=h.replace("    app.state_set('hold_intelligence_at',datetime.now().isoformat()); app.state_set('hold_intelligence_phase',phase); app.state_set('hold_intelligence_count',len(out))\n","    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat()); app.state_set('hold_intelligence_phase',phase); app.state_set('hold_intelligence_count',len(out)); app.state_set('hold_intelligence_status','READY')\n",1)
p.write_text(h,encoding='utf-8')

# Worker: only synchronize empty Top5 after a successful Dynamic calculation.
patch('worker_base.py',
"    dynamic = pd.DataFrame()\n    try:\n        dynamic = dynamic_engine.update(\n",
"    dynamic = pd.DataFrame()\n    dynamic_ok = False\n    try:\n        dynamic = dynamic_engine.update(\n",
'worker dynamic success flag')
patch('worker_base.py',
"        dynamic_count = (\n            0\n            if dynamic is None\n            else len(dynamic)\n        )\n\n        log(\n",
"        dynamic_ok = True\n        dynamic_count = (\n            0\n            if dynamic is None\n            else len(dynamic)\n        )\n\n        log(\n",
'worker dynamic success set')
patch('worker_base.py',
"    if str(source).lower() == \"full-universe\" and dynamic is not None and not dynamic.empty:\n        try:\n            event_result = top5_timeline.record_top5(app.DB, dynamic, source_phase=source, config=CFG)\n",
"    if str(source).lower() == \"full-universe\" and dynamic_ok:\n        try:\n            event_result = top5_timeline.record_top5(app.DB, dynamic, source_phase=source, config=CFG)\n",
'worker top5 authoritative empty')
patch('worker_base.py',
"def lock_premarket_picks(res, source_phase=\"full-universe\"):\n    ensure_premarket_tables()\n",
"def lock_premarket_picks(res, source_phase=\"full-universe\"):\n    # Compatibility shim only. PRELIM-R2/H10-R1 exclusively own Pre-Market writes.\n    return {\"status\":\"SCHEDULER_OWNED\",\"pick_count\":0}\n\n    ensure_premarket_tables()\n",
'legacy lock inert shim')
patch('worker_base.py',
"               WHERE r.status='LOCKED' AND r.market_date < ?\n                 AND EXISTS (SELECT 1 FROM premarket_picks p WHERE p.market_date=r.market_date)\n",
"               WHERE r.status='LOCKED' AND r.note LIKE 'H10_FINAL%' AND r.market_date < ?\n                 AND EXISTS (SELECT 1 FROM premarket_picks p WHERE p.market_date=r.market_date)\n",
'H10-only evaluation')

print('R23 STALE-STATE HYGIENE PATCH V2 APPLIED')
