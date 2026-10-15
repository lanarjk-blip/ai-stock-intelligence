from pathlib import Path


def replace_once(path, old, new, label):
    p=Path(path); s=p.read_text(encoding='utf-8')
    if old in s:
        s=s.replace(old,new,1)
    elif new not in s:
        raise SystemExit(f'{path}: missing {label}')
    p.write_text(s,encoding='utf-8')


# ---------- Dynamic Opportunity: stale-data clearing + normalized sell filter ----------
p=Path('dynamic_engine.py')
s=p.read_text(encoding='utf-8')
# Collapse repeated state writes left by temporary audit runs.
line="        app.state_set('opening_confirmation_status','READY')\n"
while line+line in s:
    s=s.replace(line+line,line,1)

anchor="""def update(res, phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        return pd.DataFrame()\n\n    prev=read_current()\n"""
new="""def _clear_dynamic_current(mode='NO_TRADE'):\n    con=dbcon()\n    try:\n        con.execute('DELETE FROM dynamic_opportunities')\n        con.execute('DELETE FROM dynamic_opportunity_pool')\n        con.commit()\n    finally:\n        con.close()\n    app.state_set('dynamic_opportunity_at',datetime.now(timezone.utc).isoformat())\n    app.state_set('dynamic_opportunity_count',0)\n    app.state_set('dynamic_candidate_mode',str(mode))\n\n\ndef update(res, phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        _clear_dynamic_current('NO_DATA')\n        return pd.DataFrame()\n\n    prev=read_current()\n"""
if anchor in s:
    s=s.replace(anchor,new,1)
elif new not in s:
    raise SystemExit('dynamic clear helper anchor missing')
old="""    base=res.copy()\n    non_sell=base[~base.decision.isin(['SELL/AVOID'])].copy()\n"""
new2="""    base=res.copy()\n    blocked={'SELL','SELL/AVOID','AVOID','STRONG_SELL','EXIT'}\n    decisions=base.decision.astype(str).str.upper().str.strip()\n    non_sell=base[~decisions.isin(blocked)].copy()\n"""
if old in s:
    s=s.replace(old,new2,1)
elif new2 not in s:
    raise SystemExit('dynamic sell filter anchor missing')
old="""        if c.empty:\n            app.state_set('dynamic_candidate_mode','NO_TRADE')\n            app.state_set('dynamic_opportunity_count',0)\n            return pd.DataFrame()\n"""
new3="""        if c.empty:\n            _clear_dynamic_current('NO_TRADE')\n            return pd.DataFrame()\n"""
if old in s:
    s=s.replace(old,new3,1)
elif new3 not in s:
    raise SystemExit('dynamic no-trade clear anchor missing')
p.write_text(s,encoding='utf-8')


# ---------- Top5: collapse duplicate patch blocks and clear stale baselines ----------
p=Path('top5_timeline.py'); s=p.read_text(encoding='utf-8')
block="""        actionable_tickers = set()\n        if dynamic is not None and not dynamic.empty:\n            for _, item in dynamic.iterrows():\n                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':\n                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())\n"""
while block+block in s:
    s=s.replace(block+block,block,1)
old="""    new = _ranked(dynamic)\n    if not new:\n        return {'status': 'SKIPPED_EMPTY', 'events': 0}\n\n    ensure_tables(db_path)\n    market = session_status(config)\n"""
new="""    new = _ranked(dynamic)\n    ensure_tables(db_path)\n    market = session_status(config)\n"""
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('top5 early-empty anchor missing')
anchor="""    with _connect(db_path) as con:\n        con.execute('BEGIN IMMEDIATE')\n        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()\n        if row is None:\n"""
new4="""    with _connect(db_path) as con:\n        con.execute('BEGIN IMMEDIATE')\n        row = con.execute('SELECT tickers_json FROM top5_live_state WHERE singleton=1').fetchone()\n        if not new:\n            old=[]\n            if row is not None:\n                try:\n                    old=json.loads(row[0]) or []\n                except (ValueError,TypeError):\n                    old=[]\n                con.execute('UPDATE top5_live_state SET updated_at=?,tickers_json=? WHERE singleton=1',\n                            (utc_iso,json.dumps([])))\n            else:\n                con.execute('INSERT INTO top5_live_state(singleton,updated_at,tickers_json) VALUES(1,?,?)',\n                            (utc_iso,json.dumps([])))\n            con.execute("UPDATE live_action_state SET updated_at=?,state='INACTIVE',entry_status='EMPTY',is_entry_ready=0",\n                        (utc_iso,))\n            con.execute("UPDATE top5_timeline SET notification_status='CANCELLED_STALE' "\n                        "WHERE notification_status IN ('PENDING','RETRY')")\n            return {'status':'CLEARED_EMPTY','events':0,'top5':[],'previous_top5':old,'market':market.get('status')}\n        if row is None:\n"""
if anchor in s:
    s=s.replace(anchor,new4,1)
elif new4 not in s:
    raise SystemExit('top5 empty clear anchor missing')
p.write_text(s,encoding='utf-8')


# ---------- Hold Intelligence: never leave old candidates visible ----------
p=Path('hold_engine.py'); h=p.read_text(encoding='utf-8')
anchor="""def update(res,phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty: return pd.DataFrame()\n"""
new="""def _clear_current(status='NO_CANDIDATE'):\n    con=dbcon()\n    try:\n        con.execute('DELETE FROM hold_intelligence')\n        con.commit()\n    finally:\n        con.close()\n    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat())\n    app.state_set('hold_intelligence_count',0)\n    app.state_set('hold_intelligence_status',status)\n\n\ndef update(res,phase='CLOSED'):\n    ensure_tables()\n    if res is None or res.empty:\n        _clear_current('NO_DATA')\n        return pd.DataFrame()\n"""
if anchor in h:
    h=h.replace(anchor,new,1)
elif new not in h:
    raise SystemExit('hold clear helper anchor missing')
h=h.replace("    if c.empty: return pd.DataFrame()\n","    if c.empty:\n        _clear_current('NO_CANDIDATE')\n        return pd.DataFrame()\n",1)
h=h.replace("    app.state_set('hold_intelligence_at',datetime.now().isoformat()); app.state_set('hold_intelligence_phase',phase); app.state_set('hold_intelligence_count',len(out))\n","    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat()); app.state_set('hold_intelligence_phase',phase); app.state_set('hold_intelligence_count',len(out)); app.state_set('hold_intelligence_status','READY')\n",1)
p.write_text(h,encoding='utf-8')


# ---------- Worker: distinguish dynamic failure from genuine empty; H10-only evaluation ----------
p=Path('worker_base.py'); w=p.read_text(encoding='utf-8')
old="""    dynamic = pd.DataFrame()\n    try:\n        dynamic = dynamic_engine.update(\n"""
new="""    dynamic = pd.DataFrame()\n    dynamic_ok = False\n    try:\n        dynamic = dynamic_engine.update(\n"""
if old in w:
    w=w.replace(old,new,1)
elif new not in w:
    raise SystemExit('worker dynamic flag anchor missing')
old="""        dynamic_count = (\n            0\n            if dynamic is None\n            else len(dynamic)\n        )\n\n        log(\n"""
new="""        dynamic_ok = True\n        dynamic_count = (\n            0\n            if dynamic is None\n            else len(dynamic)\n        )\n\n        log(\n"""
if old in w:
    w=w.replace(old,new,1)
elif new not in w:
    raise SystemExit('worker dynamic ok anchor missing')
old="""    if str(source).lower() == "full-universe" and dynamic is not None and not dynamic.empty:\n        try:\n            event_result = top5_timeline.record_top5(app.DB, dynamic, source_phase=source, config=CFG)\n"""
new="""    if str(source).lower() == "full-universe" and dynamic_ok:\n        try:\n            event_result = top5_timeline.record_top5(app.DB, dynamic, source_phase=source, config=CFG)\n"""
if old in w:
    w=w.replace(old,new,1)
elif new not in w:
    raise SystemExit('worker top5 empty synchronization anchor missing')
# Make legacy function inert even if future code accidentally calls it.
anchor="""def lock_premarket_picks(res, source_phase="full-universe"):\n    ensure_premarket_tables()\n"""
new="""def lock_premarket_picks(res, source_phase="full-universe"):\n    # Compatibility shim only. PRELIM-R2/H10-R1 exclusively own Pre-Market writes.\n    return {"status":"SCHEDULER_OWNED","pick_count":0}\n\n    ensure_premarket_tables()\n"""
if anchor in w:
    w=w.replace(anchor,new,1)
elif new not in w:
    raise SystemExit('legacy lock shim anchor missing')
# Align worker-created schema with scheduler schema and migrate old DBs.
old="""            confidence REAL, ai_score REAL, risk TEXT,\n            entry_low REAL, entry_high REAL, stop_loss REAL,\n"""
new="""            confidence REAL, ai_score REAL, risk TEXT,\n            reference_price REAL,\n            entry_low REAL, entry_high REAL, stop_loss REAL,\n"""
if old in w:
    w=w.replace(old,new,1)
old="""        con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (\n            market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,\n            status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")\n        con.commit()\n"""
new="""        con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (\n            market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,\n            status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")\n        existing={str(r[1]) for r in con.execute('PRAGMA table_info(premarket_picks)').fetchall()}\n        if 'reference_price' not in existing:\n            con.execute('ALTER TABLE premarket_picks ADD COLUMN reference_price REAL')\n        con.commit()\n"""
if old in w:
    w=w.replace(old,new,1)
elif new not in w:
    raise SystemExit('worker premarket schema migration anchor missing')
# Only official H10 final locks enter calibration.
old="""               WHERE r.status='LOCKED' AND r.market_date < ?\n                 AND EXISTS (SELECT 1 FROM premarket_picks p WHERE p.market_date=r.market_date)\n"""
new="""               WHERE r.status='LOCKED' AND r.note LIKE 'H10_FINAL%' AND r.market_date < ?\n                 AND EXISTS (SELECT 1 FROM premarket_picks p WHERE p.market_date=r.market_date)\n"""
if old in w:
    w=w.replace(old,new,1)
elif new not in w:
    raise SystemExit('H10-only evaluation anchor missing')
p.write_text(w,encoding='utf-8')

print('R23 STALE-STATE HYGIENE PATCH APPLIED')
