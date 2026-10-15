from pathlib import Path


def block(lines):
    return "\n".join(lines) + "\n"


def swap(path, old_lines, new_lines):
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    old = block(old_lines)
    new = block(new_lines)
    if old in s:
        s = s.replace(old, new, 1)
        p.write_text(s, encoding="utf-8")
        return
    if new in s:
        return
    raise SystemExit(f"anchor missing in {path}: {old_lines[0]!r}")


# ---------------- Pre-Market schema + reference price ----------------
swap("premarket_scheduler.py", [
    "def _is_trading_day(local: datetime) -> bool:",
    "    holidays = {str(x) for x in (CFG.get(\"market_holidays\") or [])}",
    "    return local.weekday() < 5 and local.date().isoformat() not in holidays",
], [
    "def _is_trading_day(local: datetime) -> bool:",
    "    return market_session.is_trading_date(local.date(), CFG)",
])

swap("premarket_scheduler.py", [
    "        confidence REAL, ai_score REAL, risk TEXT,",
    "        entry_low REAL, entry_high REAL, stop_loss REAL,",
], [
    "        confidence REAL, ai_score REAL, risk TEXT,",
    "        reference_price REAL,",
    "        entry_low REAL, entry_high REAL, stop_loss REAL,",
])

swap("premarket_scheduler.py", [
    "    con.execute(\"\"\"CREATE TABLE IF NOT EXISTS premarket_runs (",
    "        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,",
    "        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)\"\"\")",
    "    con.commit()",
], [
    "    con.execute(\"\"\"CREATE TABLE IF NOT EXISTS premarket_runs (",
    "        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,",
    "        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)\"\"\")",
    "    existing = {str(r[1]) for r in con.execute(\"PRAGMA table_info(premarket_picks)\").fetchall()}",
    "    if \"reference_price\" not in existing:",
    "        con.execute(\"ALTER TABLE premarket_picks ADD COLUMN reference_price REAL\")",
    "    con.commit()",
])

swap("premarket_scheduler.py", [
    "        \"risk\": str(row.get(\"risk\") or \"\"),",
    "        \"entry_low\": lo,",
], [
    "        \"risk\": str(row.get(\"risk\") or \"\"),",
    "        \"reference_price\": close,",
    "        \"entry_low\": lo,",
])

swap("premarket_scheduler.py", [
    "                (market_date,generated_at,rank,ticker,company,decision,confidence,",
    "                 ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,",
    "                 ihsg_regime,reason,source_phase)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
], [
    "                (market_date,generated_at,rank,ticker,company,decision,confidence,",
    "                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,",
    "                 ihsg_regime,reason,source_phase)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
])

swap("premarket_scheduler.py", [
    "                    _number(item.get(\"confidence\")), _number(item.get(\"ai_score\")),",
    "                    str(item.get(\"risk\") or \"\"), _number(item.get(\"entry_low\")),",
], [
    "                    _number(item.get(\"confidence\")), _number(item.get(\"ai_score\")),",
    "                    str(item.get(\"risk\") or \"\"), _number(item.get(\"reference_price\")),",
    "                    _number(item.get(\"entry_low\")),",
])

swap("premarket_preliminary.py", [
    "                (market_date,generated_at,rank,ticker,company,decision,confidence,",
    "                 ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,",
    "                 ihsg_regime,reason,source_phase)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
], [
    "                (market_date,generated_at,rank,ticker,company,decision,confidence,",
    "                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,",
    "                 ihsg_regime,reason,source_phase)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
])

swap("premarket_preliminary.py", [
    "                    h10._number(item.get(\"ai_score\")),",
    "                    str(item.get(\"risk\") or \"\"),",
    "                    h10._number(item.get(\"entry_low\")),",
], [
    "                    h10._number(item.get(\"ai_score\")),",
    "                    str(item.get(\"risk\") or \"\"),",
    "                    h10._number(item.get(\"reference_price\")),",
    "                    h10._number(item.get(\"entry_low\")),",
])


# ---------------- Dynamic Opportunity actionability ----------------
swap("dynamic_engine.py", [
    "import pandas as pd",
    "",
    "import app",
], [
    "import pandas as pd",
    "",
    "import app",
    "import market_session",
])

swap("dynamic_engine.py", [
    "        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,",
    "        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,",
], [
    "        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,",
    "        decision TEXT, candidate_mode TEXT,",
    "        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,",
])

swap("dynamic_engine.py", [
    "        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,",
    "        close REAL, risk TEXT, ai_score REAL, p_up REAL, p_down REAL, confidence REAL,",
], [
    "        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,",
    "        candidate_mode TEXT, close REAL, risk TEXT, ai_score REAL, p_up REAL, p_down REAL, confidence REAL,",
])

swap("dynamic_engine.py", [
    "    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_adjustment','REAL')]:",
    "        if col not in existing: con.execute(f'ALTER TABLE dynamic_opportunities ADD COLUMN {col} {typ}')",
    "    con.commit(); con.close()",
], [
    "    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_adjustment','REAL'),('decision','TEXT'),('candidate_mode','TEXT')]:",
    "        if col not in existing: con.execute(f'ALTER TABLE dynamic_opportunities ADD COLUMN {col} {typ}')",
    "    pool_existing={str(r[1]) for r in con.execute('PRAGMA table_info(dynamic_opportunity_pool)').fetchall()}",
    "    if 'candidate_mode' not in pool_existing:",
    "        con.execute('ALTER TABLE dynamic_opportunity_pool ADD COLUMN candidate_mode TEXT')",
    "    con.commit(); con.close()",
])

swap("dynamic_engine.py", [
    "def _state(row, ts, score):",
    "    decision=str(row.decision)",
    "    es=str(ts.get('entry_status','NEUTRAL'))",
], [
    "def _state(row, ts, score):",
    "    decision=str(row.decision)",
    "    es=str(ts.get('entry_status','NEUTRAL'))",
    "    mode=str(getattr(row,'candidate_mode','') or '').upper()",
    "    if mode=='MONITOR_FALLBACK': return 'MONITOR'",
])

# ---------------- Opening Confirmation: H10-only, same-day data ----------------
swap("dynamic_engine.py", [
    "                premarket_rank INTEGER,",
    "                company TEXT,",
    "                reference_price REAL,",
], [
    "                premarket_rank INTEGER,",
    "                company TEXT,",
    "                premarket_decision TEXT,",
    "                reference_price REAL,",
])

swap("dynamic_engine.py", [
    "        con.commit()",
    "    finally:",
    "        con.close()",
    "",
    "",
    "def _market_date_wib():",
], [
    "        existing={str(r[1]) for r in con.execute('PRAGMA table_info(opening_confirmation)').fetchall()}",
    "        if 'premarket_decision' not in existing:",
    "            con.execute('ALTER TABLE opening_confirmation ADD COLUMN premarket_decision TEXT')",
    "        con.commit()",
    "    finally:",
    "        con.close()",
    "",
    "",
    "def _market_date_wib(now=None):",
])

swap("dynamic_engine.py", [
    "    now = datetime.now(timezone.utc) + pd.Timedelta(hours=7)",
    "    return now.date().isoformat()",
], [
    "    return str(market_session.session_status(CFG, now=now).get('market_date') or '')",
])

swap("dynamic_engine.py", [
    "def _latest_intraday_open(ticker):",
    "    \"\"\"Return today's first intraday price when available.\"\"\"",
], [
    "def _latest_intraday_open(ticker, market_date=None):",
    "    \"\"\"Return the target market day's first intraday price when available.\"\"\"",
])

swap("dynamic_engine.py", [
    "        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')",
    "        today=(datetime.now(timezone.utc)+pd.Timedelta(hours=7)).date()",
    "        d=d[d['wib'].dt.date==today]",
], [
    "        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')",
    "        target=str(market_date or _market_date_wib())",
    "        d=d[d['wib'].dt.date.astype(str)==target]",
])

swap("dynamic_engine.py", [
    "def _daily_open_fallback(ticker):",
    "    \"\"\"Fallback to latest daily candle when intraday opening data is unavailable.\"\"\"",
    "    try:",
    "        con=dbcon()",
    "        row=con.execute(",
    "            \"\"\"SELECT open,close FROM prices",
    "               WHERE ticker=?",
    "               ORDER BY date DESC LIMIT 1\"\"\",",
    "            (str(ticker).upper(),)",
    "        ).fetchone()",
    "        con.close()",
    "        if not row:",
    "            return None,None",
    "        op=float(row[0]) if row[0] is not None else None",
    "        cur=float(row[1]) if row[1] is not None else op",
], [
    "def _daily_open_fallback(ticker, market_date=None):",
    "    \"\"\"Fallback only to a daily candle from the same market date.\"\"\"",
    "    try:",
    "        con=dbcon()",
    "        row=con.execute(",
    "            \"\"\"SELECT date,open,close FROM prices",
    "               WHERE ticker=?",
    "               ORDER BY date DESC LIMIT 1\"\"\",",
    "            (str(ticker).upper(),)",
    "        ).fetchone()",
    "        con.close()",
    "        if not row or str(row[0]) != str(market_date or _market_date_wib()):",
    "            return None,None",
    "        op=float(row[1]) if row[1] is not None else None",
    "        cur=float(row[2]) if row[2] is not None else op",
])

swap("dynamic_engine.py", [
    "def update_opening_confirmation():",
    "    \"\"\"",
    "    Evaluate the immutable Pre-Market picks against the actual opening/current price.",
    "    Does not rewrite the Pre-Market list.",
    "    \"\"\"",
    "    _ensure_opening_tables()",
    "    market_date=_market_date_wib()",
    "",
    "    con=dbcon()",
    "    try:",
    "        picks=pd.read_sql_query(",
    "            \"\"\"SELECT market_date,rank,ticker,company,reference_price,",
    "                      entry_low,entry_high,stop_loss,target_1,target_2",
    "               FROM premarket_picks",
    "               WHERE market_date=?",
    "               ORDER BY rank\"\"\",",
    "            con, params=(market_date,)",
    "        )",
    "    except Exception:",
    "        con.close()",
    "        return pd.DataFrame()",
    "    con.close()",
], [
    "def update_opening_confirmation(now=None):",
    "    \"\"\"Evaluate only immutable H-10 picks against the actual same-day open.\"\"\"",
    "    _ensure_opening_tables()",
    "    session=market_session.session_status(CFG, now=now)",
    "    session_status=str(session.get('status') or '').upper()",
    "    market_date=str(session.get('market_date') or _market_date_wib(now))",
    "    if session_status in {'PRE_MARKET','WEEKEND','HOLIDAY'}:",
    "        app.state_set('opening_confirmation_status',f'BLOCKED_{session_status}')",
    "        return pd.DataFrame()",
    "",
    "    con=dbcon()",
    "    try:",
    "        run=con.execute('SELECT status,note FROM premarket_runs WHERE market_date=?',(market_date,)).fetchone()",
    "        if not run or str(run[0]).upper()!='LOCKED' or not str(run[1] or '').startswith('H10_FINAL'):",
    "            app.state_set('opening_confirmation_status','BLOCKED_NO_H10_FINAL')",
    "            return pd.DataFrame()",
    "        picks=pd.read_sql_query(",
    "            \"\"\"SELECT market_date,rank,ticker,company,decision,reference_price,",
    "                      entry_low,entry_high,stop_loss,target_1,target_2",
    "               FROM premarket_picks",
    "               WHERE market_date=?",
    "               ORDER BY rank\"\"\",",
    "            con, params=(market_date,)",
    "        )",
    "    finally:",
    "        con.close()",
])

swap("dynamic_engine.py", [
    "        ticker=str(r.ticker)",
    "        op,cur=_latest_intraday_open(ticker)",
    "        if op is None:",
    "            op,cur=_daily_open_fallback(ticker)",
], [
    "        ticker=str(r.ticker)",
    "        original_decision=str(r.decision or 'MONITOR').upper().strip()",
    "        actionable=original_decision in {'BUY','STRONG_BUY'}",
    "        op,cur=_latest_intraday_open(ticker,market_date)",
    "        if op is None:",
    "            op,cur=_daily_open_fallback(ticker,market_date)",
])

swap("dynamic_engine.py", [
    "            elif lo <= op <= hi:",
    "                status='BUY'",
    "                reason=f'Open {op:,.2f} masih di dalam entry zone {lo:,.2f}-{hi:,.2f}.'",
    "            else:",
    "                status='STILL STRONG'",
    "                reason=f'Open {op:,.2f} masih dekat setup; pantau konfirmasi sebelum entry.'",
], [
    "            elif lo <= op <= hi:",
    "                if actionable:",
    "                    status='BUY'",
    "                    reason=f'Open {op:,.2f} masih di dalam entry zone {lo:,.2f}-{hi:,.2f} dan H-10 decision BUY.'",
    "                else:",
    "                    status='WAIT'",
    "                    reason=f'Open masuk entry zone, tetapi H-10 decision {original_decision}; tidak di-upgrade menjadi BUY.'",
    "            else:",
    "                status='STILL STRONG' if actionable else 'WAIT'",
    "                reason=(f'Open {op:,.2f} masih dekat setup; pantau konfirmasi sebelum entry.' if actionable",
    "                        else f'H-10 decision {original_decision}; tetap WAIT meski harga dekat setup.')",
])

swap("dynamic_engine.py", [
    "            'company':str(r['company']),",
    "            'reference_price':float(r['reference_price']) if pd.notna(r['reference_price']) else None,",
], [
    "            'company':str(r['company']),",
    "            'premarket_decision':original_decision,",
    "            'reference_price':float(r['reference_price']) if pd.notna(r['reference_price']) else None,",
])

swap("dynamic_engine.py", [
    "                (market_date,ticker,generated_at,premarket_rank,company,",
    "                 reference_price,open_price,current_price,entry_low,entry_high,",
    "                 gap_pct,status,reason)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
], [
    "                (market_date,ticker,generated_at,premarket_rank,company,premarket_decision,",
    "                 reference_price,open_price,current_price,entry_low,entry_high,",
    "                 gap_pct,status,reason)",
    "                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
])

swap("dynamic_engine.py", [
    "                r.company,r.reference_price,r.open_price,r.current_price,",
    "                r.entry_low,r.entry_high,r.gap_pct,r.status,r.reason",
], [
    "                r.company,r.premarket_decision,r.reference_price,r.open_price,r.current_price,",
    "                r.entry_low,r.entry_high,r.gap_pct,r.status,r.reason",
])

swap("dynamic_engine.py", [
    "        app.state_set('opening_confirmation_at',now)",
    "        app.state_set('opening_confirmation_count',len(out))",
], [
    "        app.state_set('opening_confirmation_at',now)",
    "        app.state_set('opening_confirmation_count',len(out))",
    "        app.state_set('opening_confirmation_status','READY')",
])

# ---------------- Persist candidate_mode + decision ----------------
swap("dynamic_engine.py", [
    "        con.execute('''INSERT OR REPLACE INTO dynamic_opportunity_pool",
    "            (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,decision)",
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',",
    "            (str(pr.ticker),str(pr.company),now,int(pool_rank)+1,float(pr.score),pool_state,",
    "             str(pts.get('entry_status','NEUTRAL')),str(pts.get('horizon','EOD')),float(pr.close),str(pr.risk),",
], [
    "        con.execute('''INSERT OR REPLACE INTO dynamic_opportunity_pool",
    "            (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,candidate_mode,close,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,decision)",
    "            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',",
    "            (str(pr.ticker),str(pr.company),now,int(pool_rank)+1,float(pr.score),pool_state,",
    "             str(pts.get('entry_status','NEUTRAL')),str(pts.get('horizon','EOD')),str(pr.candidate_mode),float(pr.close),str(pr.risk),",
])

swap("dynamic_engine.py", [
    "        vals=(rr.ticker,rr.company,now,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),str(ts.get('horizon','EOD')),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.target_2,rr.risk_reward,rr.risk,rr.ai_score,rr.p_up,rr.p_down,rr.confidence,rr.relative_percentile,rr.relative_rank_score,float(ts.get('edge',0.0)),float(ts.get('confidence',0.0)),reason,replacement,int(rr.stable),(None if prev_rank_missing else int(prev_rank)),rr.prev_score,rr.market_regime,rr.market_score,rr.ihsg_alignment,rr.regime_adjustment)",
    "        con.execute('''INSERT OR REPLACE INTO dynamic_opportunities (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,reason,replacement_reason,stable_cycles,previous_rank,previous_score,market_regime,market_score,ihsg_alignment,regime_adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)",
], [
    "        vals=(rr.ticker,rr.company,now,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),str(ts.get('horizon','EOD')),str(rr.decision),str(rr.candidate_mode),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.target_2,rr.risk_reward,rr.risk,rr.ai_score,rr.p_up,rr.p_down,rr.confidence,rr.relative_percentile,rr.relative_rank_score,float(ts.get('edge',0.0)),float(ts.get('confidence',0.0)),reason,replacement,int(rr.stable),(None if prev_rank_missing else int(prev_rank)),rr.prev_score,rr.market_regime,rr.market_score,rr.ihsg_alignment,rr.regime_adjustment)",
    "        con.execute('''INSERT OR REPLACE INTO dynamic_opportunities (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,decision,candidate_mode,close,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,reason,replacement_reason,stable_cycles,previous_rank,previous_score,market_regime,market_score,ihsg_alignment,regime_adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)",
])

swap("dynamic_engine.py", [
    "        out.append({'rank':rank,'ticker':rr.ticker,'company':rr.company,'opportunity_score':rr.score,'state':state,'entry_status':str(ts.get('entry_status','NEUTRAL')),'horizon':str(ts.get('horizon','EOD')),'close':rr.close,'entry_low':rr.entry_low,'entry_high':rr.entry_high,'stop_loss':rr.stop_loss,'target_1':rr.target_1,'target_2':rr.target_2,'risk_reward':rr.risk_reward,'risk':rr.risk,'ai_score':rr.ai_score,'p_up':rr.p_up,'p_down':rr.p_down,'confidence':rr.confidence,'relative_percentile':rr.relative_percentile,'time_edge':float(ts.get('edge',0.0)),'time_confidence':float(ts.get('confidence',0.0)),'reason':reason,'replacement_reason':replacement,'stable_cycles':int(rr.stable),'market_regime':rr.market_regime,'market_score':rr.market_score,'ihsg_alignment':rr.ihsg_alignment,'regime_adjustment':rr.regime_adjustment,'candidate_mode':rr.candidate_mode})",
], [
    "        out.append({'rank':rank,'ticker':rr.ticker,'company':rr.company,'opportunity_score':rr.score,'state':state,'entry_status':str(ts.get('entry_status','NEUTRAL')),'horizon':str(ts.get('horizon','EOD')),'decision':rr.decision,'close':rr.close,'entry_low':rr.entry_low,'entry_high':rr.entry_high,'stop_loss':rr.stop_loss,'target_1':rr.target_1,'target_2':rr.target_2,'risk_reward':rr.risk_reward,'risk':rr.risk,'ai_score':rr.ai_score,'p_up':rr.p_up,'p_down':rr.p_down,'confidence':rr.confidence,'relative_percentile':rr.relative_percentile,'time_edge':float(ts.get('edge',0.0)),'time_confidence':float(ts.get('confidence',0.0)),'reason':reason,'replacement_reason':replacement,'stable_cycles':int(rr.stable),'market_regime':rr.market_regime,'market_score':rr.market_score,'ihsg_alignment':rr.ihsg_alignment,'regime_adjustment':rr.regime_adjustment,'candidate_mode':rr.candidate_mode})",
])

swap("dynamic_engine.py", [
    "    try:",
    "        update_opening_confirmation()",
    "    except Exception:",
    "        pass",
], [
    "    try:",
    "        update_opening_confirmation()",
    "    except Exception as exc:",
    "        app.state_set('opening_confirmation_status','ERROR')",
    "        app.state_set('opening_confirmation_error',str(exc)[:500])",
])


# ---------------- Top-5/ENTRY READY must honor fallback mode ----------------
swap("top5_timeline.py", [
    "        state = str(_row_value(row, 'state', '') or '').strip().upper()",
    "        entry = str(_row_value(row, 'entry_status', '') or '').strip().upper()",
    "        ready = int(state == 'ENTRY READY' and entry == 'ACTIVE')",
], [
    "        state = str(_row_value(row, 'state', '') or '').strip().upper()",
    "        entry = str(_row_value(row, 'entry_status', '') or '').strip().upper()",
    "        mode = str(_row_value(row, 'candidate_mode', '') or '').strip().upper()",
    "        ready = int(mode != 'MONITOR_FALLBACK' and state == 'ENTRY READY' and entry == 'ACTIVE')",
])

swap("top5_timeline.py", [
    "        events = []",
    "        entered = [t for t in new if t not in old]",
    "        exited = [t for t in old if t not in new]",
], [
    "        events = []",
    "        entered = [t for t in new if t not in old]",
    "        exited = [t for t in old if t not in new]",
    "        actionable_tickers = set()",
    "        if dynamic is not None and not dynamic.empty:",
    "            for _, item in dynamic.iterrows():",
    "                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':",
    "                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())",
])

swap("top5_timeline.py", [
    "            for i, ticker in enumerate(entered):",
    "                replaced = exited[i] if i < len(exited) else ''",
], [
    "            for i, ticker in enumerate(entered):",
    "                if ticker not in actionable_tickers:",
    "                    continue",
    "                replaced = exited[i] if i < len(exited) else ''",
])


# ---------------- Recommendation history vs actual paper trades ----------------
swap("recommendation_history.py", [
    "            recommendation TEXT,",
    "            opportunity_state TEXT,",
], [
    "            recommendation TEXT,",
    "            candidate_mode TEXT,",
    "            opportunity_state TEXT,",
])

swap("recommendation_history.py", [
    "    con.execute(\"CREATE INDEX IF NOT EXISTS idx_rec_hist_ticker_date ON recommendation_history(ticker, market_date)\")",
], [
    "    existing = {str(r[1]) for r in con.execute(\"PRAGMA table_info(recommendation_history)\").fetchall()}",
    "    if \"candidate_mode\" not in existing:",
    "        con.execute(\"ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT\")",
    "    con.execute(\"CREATE INDEX IF NOT EXISTS idx_rec_hist_ticker_date ON recommendation_history(ticker, market_date)\")",
])

swap("recommendation_history.py", [
    "            recommendation = _s(row.get(\"decision\", row.get(\"state\", \"WATCH\")), \"WATCH\")",
    "            vals = (",
    "                run_id, generated_at, mdate, ticker, _s(row.get(\"company\")), rank,",
    "                recommendation, _s(row.get(\"state\")), _s(row.get(\"entry_status\")),",
], [
    "            recommendation = _s(row.get(\"decision\", row.get(\"state\", \"WATCH\")), \"WATCH\")",
    "            candidate_mode = _s(row.get(\"candidate_mode\"), \"STRICT\").upper()",
    "            vals = (",
    "                run_id, generated_at, mdate, ticker, _s(row.get(\"company\")), rank,",
    "                recommendation, candidate_mode, _s(row.get(\"state\")), _s(row.get(\"entry_status\")),",
])

swap("recommendation_history.py", [
    "                    run_id,generated_at,market_date,ticker,company,rank,recommendation,",
    "                    opportunity_state,entry_status,opportunity_score,ai_score,p_up,p_down,confidence,",
    "                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,",
    "                    market_regime,reason",
    "                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
], [
    "                    run_id,generated_at,market_date,ticker,company,rank,recommendation,candidate_mode,",
    "                    opportunity_state,entry_status,opportunity_score,ai_score,p_up,p_down,confidence,",
    "                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,",
    "                    market_regime,reason",
    "                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",",
])

swap("recommendation_history.py", [
    "            rec_id = int(cur.lastrowid)",
    "",
    "            # One live paper position per ticker. New recommendations remain in",
], [
    "            rec_id = int(cur.lastrowid)",
    "",
    "            actionable = (",
    "                candidate_mode != \"MONITOR_FALLBACK\"",
    "                and recommendation.upper() in {\"BUY\", \"STRONG_BUY\"}",
    "            )",
    "            if not actionable:",
    "                continue",
    "",
    "            # One live paper position per ticker. New recommendations remain in",
])


# ---------------- Human-facing build labels: stable, fingerprint is authoritative ----------------
swap("dynamic_opportunity_ui.py", [
    "RUNTIME_BUILD_MARKER = 'V1.8.52-20261015-R20'",
], [
    "RUNTIME_BUILD_MARKER = 'V1.8.52'",
])

swap("runtime_supervisor.py", [
    '_BASE_BUILD = "V1.8.52-20261015-R21-SINGLETON"',
], [
    '_BASE_BUILD = "V1.8.52-SINGLETON"',
])

print("R23 ACTIONABILITY PATCH V2 APPLIED")
