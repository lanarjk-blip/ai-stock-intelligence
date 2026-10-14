from pathlib import Path


def patch(path, replacements):
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    for old, new in replacements:
        if old in s:
            s = s.replace(old, new, 1)
        elif new not in s:
            raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    p.write_text(s, encoding='utf-8')


patch('premarket_scheduler.py', [
    (
'''def _is_trading_day(local: datetime) -> bool:\n    holidays = {str(x) for x in (CFG.get("market_holidays") or [])}\n    return local.weekday() < 5 and local.date().isoformat() not in holidays\n''',
'''def _is_trading_day(local: datetime) -> bool:\n    return market_session.is_trading_date(local.date(), CFG)\n'''
    ),
    (
'''        confidence REAL, ai_score REAL, risk TEXT,\n        entry_low REAL, entry_high REAL, stop_loss REAL,\n''',
'''        confidence REAL, ai_score REAL, risk TEXT,\n        reference_price REAL,\n        entry_low REAL, entry_high REAL, stop_loss REAL,\n'''
    ),
    (
'''    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (\n        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,\n        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")\n    con.commit()\n''',
'''    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (\n        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,\n        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")\n    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(premarket_picks)").fetchall()}\n    if "reference_price" not in existing:\n        con.execute("ALTER TABLE premarket_picks ADD COLUMN reference_price REAL")\n    con.commit()\n'''
    ),
    (
'''        "risk": str(row.get("risk") or ""),\n        "entry_low": lo,\n''',
'''        "risk": str(row.get("risk") or ""),\n        "reference_price": close,\n        "entry_low": lo,\n'''
    ),
    (
'''                (market_date,generated_at,rank,ticker,company,decision,confidence,\n                 ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,\n                 ihsg_regime,reason,source_phase)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n''',
'''                (market_date,generated_at,rank,ticker,company,decision,confidence,\n                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,\n                 ihsg_regime,reason,source_phase)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n'''
    ),
    (
'''                    _number(item.get("confidence")), _number(item.get("ai_score")),\n                    str(item.get("risk") or ""), _number(item.get("entry_low")),\n''',
'''                    _number(item.get("confidence")), _number(item.get("ai_score")),\n                    str(item.get("risk") or ""), _number(item.get("reference_price")),\n                    _number(item.get("entry_low")),\n'''
    ),
])


patch('premarket_preliminary.py', [
    (
'''                (market_date,generated_at,rank,ticker,company,decision,confidence,\n                 ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,\n                 ihsg_regime,reason,source_phase)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n''',
'''                (market_date,generated_at,rank,ticker,company,decision,confidence,\n                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,\n                 ihsg_regime,reason,source_phase)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n'''
    ),
    (
'''                    h10._number(item.get("ai_score")),\n                    str(item.get("risk") or ""),\n                    h10._number(item.get("entry_low")),\n''',
'''                    h10._number(item.get("ai_score")),\n                    str(item.get("risk") or ""),\n                    h10._number(item.get("reference_price")),\n                    h10._number(item.get("entry_low")),\n'''
    ),
])


patch('dynamic_engine.py', [
    (
'''import pandas as pd\n\nimport app\n''',
'''import pandas as pd\n\nimport app\nimport market_session\n'''
    ),
    (
'''        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,\n        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,\n''',
'''        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,\n        decision TEXT, candidate_mode TEXT,\n        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,\n'''
    ),
    (
'''        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,\n        close REAL, risk TEXT, ai_score REAL, p_up REAL, p_down REAL, confidence REAL,\n''',
'''        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,\n        candidate_mode TEXT, close REAL, risk TEXT, ai_score REAL, p_up REAL, p_down REAL, confidence REAL,\n'''
    ),
    (
'''    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_adjustment','REAL')]:\n        if col not in existing: con.execute(f'ALTER TABLE dynamic_opportunities ADD COLUMN {col} {typ}')\n    con.commit(); con.close()\n''',
'''    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_adjustment','REAL'),('decision','TEXT'),('candidate_mode','TEXT')]:\n        if col not in existing: con.execute(f'ALTER TABLE dynamic_opportunities ADD COLUMN {col} {typ}')\n    pool_existing={str(r[1]) for r in con.execute('PRAGMA table_info(dynamic_opportunity_pool)').fetchall()}\n    if 'candidate_mode' not in pool_existing:\n        con.execute('ALTER TABLE dynamic_opportunity_pool ADD COLUMN candidate_mode TEXT')\n    con.commit(); con.close()\n'''
    ),
    (
'''def _state(row, ts, score):\n    decision=str(row.decision)\n    es=str(ts.get('entry_status','NEUTRAL'))\n''',
'''def _state(row, ts, score):\n    decision=str(row.decision)\n    es=str(ts.get('entry_status','NEUTRAL'))\n    mode=str(getattr(row,'candidate_mode','') or '').upper()\n    if mode=='MONITOR_FALLBACK': return 'MONITOR'\n'''
    ),
    (
'''def _market_date_wib():\n    now = datetime.now(timezone.utc) + pd.Timedelta(hours=7)\n    return now.date().isoformat()\n''',
'''def _market_date_wib(now=None):\n    return str(market_session.session_status(CFG, now=now).get('market_date') or '')\n'''
    ),
    (
'''def _latest_intraday_open(ticker):\n    """Return today's first intraday price when available."""\n''',
'''def _latest_intraday_open(ticker, market_date=None):\n    """Return the target market day's first intraday price when available."""\n'''
    ),
    (
'''        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')\n        today=(datetime.now(timezone.utc)+pd.Timedelta(hours=7)).date()\n        d=d[d['wib'].dt.date==today]\n''',
'''        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')\n        target=str(market_date or _market_date_wib())\n        d=d[d['wib'].dt.date.astype(str)==target]\n'''
    ),
    (
'''def _daily_open_fallback(ticker):\n    """Fallback to latest daily candle when intraday opening data is unavailable."""\n    try:\n        con=dbcon()\n        row=con.execute(\n            """SELECT open,close FROM prices\n               WHERE ticker=?\n               ORDER BY date DESC LIMIT 1""",\n            (str(ticker).upper(),)\n        ).fetchone()\n        con.close()\n        if not row:\n            return None,None\n        op=float(row[0]) if row[0] is not None else None\n        cur=float(row[1]) if row[1] is not None else op\n''',
'''def _daily_open_fallback(ticker, market_date=None):\n    """Fallback only to a daily candle from the same market date."""\n    try:\n        con=dbcon()\n        row=con.execute(\n            """SELECT date,open,close FROM prices\n               WHERE ticker=?\n               ORDER BY date DESC LIMIT 1""",\n            (str(ticker).upper(),)\n        ).fetchone()\n        con.close()\n        if not row or str(row[0]) != str(market_date or _market_date_wib()):\n            return None,None\n        op=float(row[1]) if row[1] is not None else None\n        cur=float(row[2]) if row[2] is not None else op\n'''
    ),
    (
'''def update_opening_confirmation():\n    """\n    Evaluate the immutable Pre-Market picks against the actual opening/current price.\n    Does not rewrite the Pre-Market list.\n    """\n    _ensure_opening_tables()\n    market_date=_market_date_wib()\n\n    con=dbcon()\n    try:\n        picks=pd.read_sql_query(\n            """SELECT market_date,rank,ticker,company,reference_price,\n                      entry_low,entry_high,stop_loss,target_1,target_2\n               FROM premarket_picks\n               WHERE market_date=?\n               ORDER BY rank""",\n            con, params=(market_date,)\n        )\n    except Exception:\n        con.close()\n        return pd.DataFrame()\n    con.close()\n''',
'''def update_opening_confirmation(now=None):\n    """Evaluate only immutable H-10 picks against the actual same-day open."""\n    _ensure_opening_tables()\n    session=market_session.session_status(CFG, now=now)\n    session_status=str(session.get('status') or '').upper()\n    market_date=str(session.get('market_date') or _market_date_wib(now))\n    if session_status in {'PRE_MARKET','WEEKEND','HOLIDAY'}:\n        app.state_set('opening_confirmation_status',f'BLOCKED_{session_status}')\n        return pd.DataFrame()\n\n    con=dbcon()\n    try:\n        run=con.execute('SELECT status,note FROM premarket_runs WHERE market_date=?',(market_date,)).fetchone()\n        if not run or str(run[0]).upper()!='LOCKED' or not str(run[1] or '').startswith('H10_FINAL'):\n            app.state_set('opening_confirmation_status','BLOCKED_NO_H10_FINAL')\n            return pd.DataFrame()\n        picks=pd.read_sql_query(\n            """SELECT market_date,rank,ticker,company,decision,reference_price,\n                      entry_low,entry_high,stop_loss,target_1,target_2\n               FROM premarket_picks\n               WHERE market_date=?\n               ORDER BY rank""",\n            con, params=(market_date,)\n        )\n    finally:\n        con.close()\n'''
    ),
    (
'''        ticker=str(r.ticker)\n        op,cur=_latest_intraday_open(ticker)\n        if op is None:\n            op,cur=_daily_open_fallback(ticker)\n''',
'''        ticker=str(r.ticker)\n        original_decision=str(r.decision or 'MONITOR').upper().strip()\n        actionable=original_decision in {'BUY','STRONG_BUY'}\n        op,cur=_latest_intraday_open(ticker,market_date)\n        if op is None:\n            op,cur=_daily_open_fallback(ticker,market_date)\n'''
    ),
    (
'''            elif lo <= op <= hi:\n                status='BUY'\n                reason=f'Open {op:,.2f} masih di dalam entry zone {lo:,.2f}-{hi:,.2f}.'\n            else:\n                status='STILL STRONG'\n                reason=f'Open {op:,.2f} masih dekat setup; pantau konfirmasi sebelum entry.'\n''',
'''            elif lo <= op <= hi:\n                if actionable:\n                    status='BUY'\n                    reason=f'Open {op:,.2f} masih di dalam entry zone {lo:,.2f}-{hi:,.2f} dan H-10 decision BUY.'\n                else:\n                    status='WAIT'\n                    reason=f'Open masuk entry zone, tetapi H-10 decision {original_decision}; tidak di-upgrade menjadi BUY.'\n            else:\n                status='STILL STRONG' if actionable else 'WAIT'\n                reason=(f'Open {op:,.2f} masih dekat setup; pantau konfirmasi sebelum entry.' if actionable\n                        else f'H-10 decision {original_decision}; tetap WAIT meski harga dekat setup.')\n'''
    ),
    (
'''            'company':str(r['company']),\n            'reference_price':float(r['reference_price']) if pd.notna(r['reference_price']) else None,\n''',
'''            'company':str(r['company']),\n            'premarket_decision':original_decision,\n            'reference_price':float(r['reference_price']) if pd.notna(r['reference_price']) else None,\n'''
    ),
    (
'''                market_date TEXT NOT NULL,\n                ticker TEXT NOT NULL,\n                generated_at TEXT NOT NULL,\n                premarket_rank INTEGER,\n                company TEXT,\n                reference_price REAL,\n''',
'''                market_date TEXT NOT NULL,\n                ticker TEXT NOT NULL,\n                generated_at TEXT NOT NULL,\n                premarket_rank INTEGER,\n                company TEXT,\n                premarket_decision TEXT,\n                reference_price REAL,\n'''
    ),
    (
'''        con.commit()\n    finally:\n        con.close()\n\n\ndef _market_date_wib''',
'''        existing={str(r[1]) for r in con.execute('PRAGMA table_info(opening_confirmation)').fetchall()}\n        if 'premarket_decision' not in existing:\n            con.execute('ALTER TABLE opening_confirmation ADD COLUMN premarket_decision TEXT')\n        con.commit()\n    finally:\n        con.close()\n\n\ndef _market_date_wib'''
    ),
    (
'''                (market_date,ticker,generated_at,premarket_rank,company,\n                 reference_price,open_price,current_price,entry_low,entry_high,\n                 gap_pct,status,reason)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)\n''',
'''                (market_date,ticker,generated_at,premarket_rank,company,premarket_decision,\n                 reference_price,open_price,current_price,entry_low,entry_high,\n                 gap_pct,status,reason)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n'''
    ),
    (
'''                r.company,r.reference_price,r.open_price,r.current_price,\n                r.entry_low,r.entry_high,r.gap_pct,r.status,r.reason\n''',
'''                r.company,r.premarket_decision,r.reference_price,r.open_price,r.current_price,\n                r.entry_low,r.entry_high,r.gap_pct,r.status,r.reason\n'''
    ),
    (
'''        app.state_set('opening_confirmation_at',now)\n        app.state_set('opening_confirmation_count',len(out))\n''',
'''        app.state_set('opening_confirmation_at',now)\n        app.state_set('opening_confirmation_count',len(out))\n        app.state_set('opening_confirmation_status','READY')\n'''
    ),
    (
'''        con.execute('''INSERT OR REPLACE INTO dynamic_opportunity_pool\n            (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,decision)\n            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',\n            (str(pr.ticker),str(pr.company),now,int(pool_rank)+1,float(pr.score),pool_state,\n             str(pts.get('entry_status','NEUTRAL')),str(pts.get('horizon','EOD')),float(pr.close),str(pr.risk),\n''',
'''        con.execute('''INSERT OR REPLACE INTO dynamic_opportunity_pool\n            (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,candidate_mode,close,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,decision)\n            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',\n            (str(pr.ticker),str(pr.company),now,int(pool_rank)+1,float(pr.score),pool_state,\n             str(pts.get('entry_status','NEUTRAL')),str(pts.get('horizon','EOD')),str(pr.candidate_mode),float(pr.close),str(pr.risk),\n'''
    ),
    (
'''        vals=(rr.ticker,rr.company,now,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),str(ts.get('horizon','EOD')),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.target_2,rr.risk_reward,rr.risk,rr.ai_score,rr.p_up,rr.p_down,rr.confidence,rr.relative_percentile,rr.relative_rank_score,float(ts.get('edge',0.0)),float(ts.get('confidence',0.0)),reason,replacement,int(rr.stable),(None if prev_rank_missing else int(prev_rank)),rr.prev_score,rr.market_regime,rr.market_score,rr.ihsg_alignment,rr.regime_adjustment)\n        con.execute('''INSERT OR REPLACE INTO dynamic_opportunities (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,reason,replacement_reason,stable_cycles,previous_rank,previous_score,market_regime,market_score,ihsg_alignment,regime_adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)\n''',
'''        vals=(rr.ticker,rr.company,now,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),str(ts.get('horizon','EOD')),str(rr.decision),str(rr.candidate_mode),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.target_2,rr.risk_reward,rr.risk,rr.ai_score,rr.p_up,rr.p_down,rr.confidence,rr.relative_percentile,rr.relative_rank_score,float(ts.get('edge',0.0)),float(ts.get('confidence',0.0)),reason,replacement,int(rr.stable),(None if prev_rank_missing else int(prev_rank)),rr.prev_score,rr.market_regime,rr.market_score,rr.ihsg_alignment,rr.regime_adjustment)\n        con.execute('''INSERT OR REPLACE INTO dynamic_opportunities (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,decision,candidate_mode,close,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,reason,replacement_reason,stable_cycles,previous_rank,previous_score,market_regime,market_score,ihsg_alignment,regime_adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)\n'''
    ),
    (
'''        out.append({'rank':rank,'ticker':rr.ticker,'company':rr.company,'opportunity_score':rr.score,'state':state,'entry_status':str(ts.get('entry_status','NEUTRAL')),'horizon':str(ts.get('horizon','EOD')),'close':rr.close,''',
'''        out.append({'rank':rank,'ticker':rr.ticker,'company':rr.company,'opportunity_score':rr.score,'state':state,'entry_status':str(ts.get('entry_status','NEUTRAL')),'horizon':str(ts.get('horizon','EOD')),'decision':rr.decision,'close':rr.close,'''
    ),
    (
'''    try:\n        update_opening_confirmation()\n    except Exception:\n        pass\n''',
'''    try:\n        update_opening_confirmation()\n    except Exception as exc:\n        app.state_set('opening_confirmation_status','ERROR')\n        app.state_set('opening_confirmation_error',str(exc)[:500])\n'''
    ),
])


patch('top5_timeline.py', [
    (
'''        state = str(_row_value(row, 'state', '') or '').strip().upper()\n        entry = str(_row_value(row, 'entry_status', '') or '').strip().upper()\n        ready = int(state == 'ENTRY READY' and entry == 'ACTIVE')\n''',
'''        state = str(_row_value(row, 'state', '') or '').strip().upper()\n        entry = str(_row_value(row, 'entry_status', '') or '').strip().upper()\n        mode = str(_row_value(row, 'candidate_mode', '') or '').strip().upper()\n        ready = int(mode != 'MONITOR_FALLBACK' and state == 'ENTRY READY' and entry == 'ACTIVE')\n'''
    ),
    (
'''        events = []\n        entered = [t for t in new if t not in old]\n        exited = [t for t in old if t not in new]\n''',
'''        events = []\n        entered = [t for t in new if t not in old]\n        exited = [t for t in old if t not in new]\n        actionable_tickers = set()\n        if dynamic is not None and not dynamic.empty:\n            for _, item in dynamic.iterrows():\n                if str(_row_value(item, 'candidate_mode', '') or '').upper() != 'MONITOR_FALLBACK':\n                    actionable_tickers.add(str(_row_value(item, 'ticker', '') or '').strip().upper())\n'''
    ),
    (
'''            for i, ticker in enumerate(entered):\n                replaced = exited[i] if i < len(exited) else ''\n                events.append(('ENTRY', ticker, old.index(ticker)+1 if ticker in old else None,\n''',
'''            for i, ticker in enumerate(entered):\n                if ticker not in actionable_tickers:\n                    continue\n                replaced = exited[i] if i < len(exited) else ''\n                events.append(('ENTRY', ticker, old.index(ticker)+1 if ticker in old else None,\n'''
    ),
])


patch('recommendation_history.py', [
    (
'''            recommendation TEXT,\n            opportunity_state TEXT,\n''',
'''            recommendation TEXT,\n            candidate_mode TEXT,\n            opportunity_state TEXT,\n'''
    ),
    (
'''    con.execute("CREATE INDEX IF NOT EXISTS idx_rec_hist_ticker_date ON recommendation_history(ticker, market_date)")\n''',
'''    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(recommendation_history)").fetchall()}\n    if "candidate_mode" not in existing:\n        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")\n    con.execute("CREATE INDEX IF NOT EXISTS idx_rec_hist_ticker_date ON recommendation_history(ticker, market_date)")\n'''
    ),
    (
'''            recommendation = _s(row.get("decision", row.get("state", "WATCH")), "WATCH")\n            vals = (\n                run_id, generated_at, mdate, ticker, _s(row.get("company")), rank,\n                recommendation, _s(row.get("state")), _s(row.get("entry_status")),\n''',
'''            recommendation = _s(row.get("decision", row.get("state", "WATCH")), "WATCH")\n            candidate_mode = _s(row.get("candidate_mode"), "STRICT").upper()\n            vals = (\n                run_id, generated_at, mdate, ticker, _s(row.get("company")), rank,\n                recommendation, candidate_mode, _s(row.get("state")), _s(row.get("entry_status")),\n'''
    ),
    (
'''                    run_id,generated_at,market_date,ticker,company,rank,recommendation,\n                    opportunity_state,entry_status,opportunity_score,ai_score,p_up,p_down,confidence,\n                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,\n                    market_regime,reason\n                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n''',
'''                    run_id,generated_at,market_date,ticker,company,rank,recommendation,candidate_mode,\n                    opportunity_state,entry_status,opportunity_score,ai_score,p_up,p_down,confidence,\n                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,\n                    market_regime,reason\n                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n'''
    ),
    (
'''            rec_id = int(cur.lastrowid)\n\n            # One live paper position per ticker. New recommendations remain in\n''',
'''            rec_id = int(cur.lastrowid)\n\n            actionable = (\n                candidate_mode != "MONITOR_FALLBACK"\n                and recommendation.upper() in {"BUY", "STRONG_BUY"}\n            )\n            if not actionable:\n                continue\n\n            # One live paper position per ticker. New recommendations remain in\n'''
    ),
])


patch('dynamic_opportunity_ui.py', [
    (
"RUNTIME_BUILD_MARKER = 'V1.8.52-20261015-R20'\n",
"RUNTIME_BUILD_MARKER = 'V1.8.52'\n"
    ),
])

patch('runtime_supervisor.py', [
    (
'_BASE_BUILD = "V1.8.52-20261015-R21-SINGLETON"\n',
'_BASE_BUILD = "V1.8.52-SINGLETON"\n'
    ),
])

print('R23 ACTIONABILITY PATCH APPLIED')
