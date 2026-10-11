import json, sqlite3
from datetime import datetime, timezone
import numpy as np
import pandas as pd

import app

CFG = app.CFGD
DB_DIR = app.DB_DIR


def dbcon():
    return app.dbcon()


def ensure_tables():
    con = dbcon()
    con.execute("""CREATE TABLE IF NOT EXISTS dynamic_opportunities (
        ticker TEXT PRIMARY KEY, company TEXT, generated_at TEXT, rank INTEGER,
        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,
        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,
        target_1 REAL, target_2 REAL, risk_reward REAL, risk TEXT,
        ai_score REAL, p_up REAL, p_down REAL, confidence REAL,
        relative_percentile REAL, relative_rank_score REAL, time_edge REAL,
        time_confidence REAL, reason TEXT, replacement_reason TEXT,
        stable_cycles INTEGER, previous_rank INTEGER, previous_score REAL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS dynamic_opportunity_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, ticker TEXT,
        rank INTEGER, opportunity_score REAL, state TEXT, entry_status TEXT,
        close REAL, entry_low REAL, entry_high REAL, stop_loss REAL,
        target_1 REAL, risk_reward REAL, risk TEXT, decision TEXT,
        relative_percentile REAL, time_edge REAL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS dynamic_opportunity_pool (
        ticker TEXT PRIMARY KEY, company TEXT, generated_at TEXT, rank INTEGER,
        opportunity_score REAL, state TEXT, entry_status TEXT, horizon TEXT,
        close REAL, risk TEXT, ai_score REAL, p_up REAL, p_down REAL, confidence REAL,
        relative_percentile REAL, relative_rank_score REAL, time_edge REAL, time_confidence REAL,
        decision TEXT
    )""")
    # Migrate tables created by V1.8.21/V1.8.22.1 without breaking the user's DB.
    existing={str(r[1]) for r in con.execute('PRAGMA table_info(dynamic_opportunities)').fetchall()}
    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_adjustment','REAL')]:
        if col not in existing: con.execute(f'ALTER TABLE dynamic_opportunities ADD COLUMN {col} {typ}')
    con.commit(); con.close()
    _ensure_opening_tables()


def read_current():
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT * FROM dynamic_opportunities ORDER BY rank',con); con.close(); return d
    except Exception:
        return pd.DataFrame()


def _cached_time_signal(ticker):
    """Use the existing V1.8.20.1 time model, but only for a small candidate set."""
    try:
        tp=app.intraday_prediction(str(ticker))
        if tp is None or tp.empty:
            return {'edge':0.0,'confidence':0.0,'horizon':'EOD','entry_status':'NEUTRAL'}
        # Prefer EOD for next-session / broad opportunity selection.
        row=tp[tp.horizon.astype(str)=='EOD']
        if row.empty: row=tp.iloc[[0]]
        r=row.iloc[0]
        return {'edge':float(r.edge),'confidence':float(r.confidence),'horizon':str(r.horizon),'entry_status':str(r.entry_status)}
    except Exception:
        return {'edge':0.0,'confidence':0.0,'horizon':'EOD','entry_status':'NEUTRAL'}


def _market_context(row):
    """Return a small, transparent IHSG regime signal for opportunity scoring."""
    regime=str(getattr(row,'market_regime','UNKNOWN'))
    market_score=float(row.market_score) if hasattr(row,'market_score') and pd.notna(row.market_score) else 50.0
    pup=float(row.jst_p_up)*100 if pd.notna(row.jst_p_up) else 50.0
    pdown=float(row.jst_p_down)*100 if pd.notna(row.jst_p_down) else 50.0
    # Alignment rewards stock strength that agrees with the broad market and
    # penalizes bullish candidates fighting a weak/volatile IHSG.
    alignment=float(np.clip(50.0 + 0.55*(market_score-50.0) + 25.0*((pup-pdown)/100.0),0,100))
    regime_adjust=float(np.clip((alignment-50.0)*0.16, -8.0, 8.0))
    if regime=='HIGH_VOLATILITY': regime_adjust-=2.0
    return regime,market_score,alignment,float(np.clip(regime_adjust,-10,8))

def _score(row, ts):
    ai=float(row.ai_score) if pd.notna(row.ai_score) else 0.0
    pct=float(row.relative_percentile)*100 if pd.notna(row.relative_percentile) else 0.0
    pup=float(row.jst_p_up)*100 if pd.notna(row.jst_p_up) else 50.0
    conf=float(row.jst_confidence)*100 if pd.notna(row.jst_confidence) else 0.0
    rr=float(row.risk_reward_2) if pd.notna(row.risk_reward_2) else 0.0
    rr_score=float(np.clip(rr/3.0,0,1)*100)
    edge=float(ts.get('edge',0.0))*100
    time_conf=float(ts.get('confidence',0.0))*100
    time_score=float(np.clip(50.0 + edge*1.5,0,100)) if time_conf>0 else 50.0
    risk=str(row.risk)
    risk_penalty={'LOW':0.0,'MEDIUM':3.0,'HIGH':7.0}.get(risk,5.0)
    _,market_score,alignment,regime_adjust=_market_context(row)
    # Keep IHSG influential but not dominant. The stock's own evidence remains primary.
    score=(0.28*ai + 0.24*pct + 0.14*pup + 0.10*conf + 0.08*rr_score + 0.07*time_score + 0.05*time_conf + 0.04*alignment) - risk_penalty + regime_adjust
    return float(np.clip(score,0,100))


def _state(row, ts, score):
    decision=str(row.decision)
    es=str(ts.get('entry_status','NEUTRAL'))
    if es=='ACTIVE' and score>=65 and decision not in ('SELL/AVOID',): return 'ENTRY READY'
    if es=='DEVELOPING' and score>=60 and decision not in ('SELL/AVOID',): return 'DEVELOPING'
    if decision in ('BUY','NEAR ENTRY'): return 'DEVELOPING'
    if decision=='WATCH': return 'WATCH'
    return 'MONITOR'


def _dynamic_entry(row, ts):
    close=float(row.close)
    lo=float(row.entry_low) if pd.notna(row.entry_low) else close
    hi=float(row.entry_high) if pd.notna(row.entry_high) else close
    edge=float(ts.get('edge',0.0))
    conf=float(ts.get('confidence',0.0))
    # Dynamic zone is a refinement of the Risk Engine zone, not a replacement.
    if edge>=0.05 and conf>=0.45:
        lo2=lo
        hi2=min(hi, close*1.005)
    elif edge<=-0.05:
        lo2=lo
        hi2=min(hi, close*0.995)
    else:
        lo2,hi2=lo,hi
    if hi2<lo2: hi2=lo2
    return float(lo2),float(hi2)



# ============================================================
# V1.8.42 STAGE 3 - OPENING CONFIRMATION
# ============================================================

def _ensure_opening_tables():
    con=dbcon()
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS opening_confirmation (
                market_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                premarket_rank INTEGER,
                company TEXT,
                reference_price REAL,
                open_price REAL,
                current_price REAL,
                entry_low REAL,
                entry_high REAL,
                gap_pct REAL,
                status TEXT,
                reason TEXT,
                PRIMARY KEY(market_date,ticker)
            )
        """)
        con.commit()
    finally:
        con.close()


def _market_date_wib():
    now = datetime.now(timezone.utc) + pd.Timedelta(hours=7)
    return now.date().isoformat()


def _latest_intraday_open(ticker):
    """Return today's first intraday price when available."""
    try:
        con=dbcon()
        d=pd.read_sql_query(
            """SELECT ts,open,close
               FROM intraday
               WHERE ticker=?
               ORDER BY ts""",
            con, params=(str(ticker).upper(),)
        )
        con.close()
        if d.empty:
            return None,None
        d['ts']=pd.to_datetime(d['ts'],utc=True,errors='coerce')
        d=d.dropna(subset=['ts'])
        if d.empty:
            return None,None
        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')
        today=(datetime.now(timezone.utc)+pd.Timedelta(hours=7)).date()
        d=d[d['wib'].dt.date==today]
        if d.empty:
            return None,None
        first=d.iloc[0]
        last=d.iloc[-1]
        op=float(first['open']) if pd.notna(first['open']) else float(first['close'])
        cur=float(last['close']) if pd.notna(last['close']) else op
        return op,cur
    except Exception:
        return None,None


def _daily_open_fallback(ticker):
    """Fallback to latest daily candle when intraday opening data is unavailable."""
    try:
        con=dbcon()
        row=con.execute(
            """SELECT open,close FROM prices
               WHERE ticker=?
               ORDER BY date DESC LIMIT 1""",
            (str(ticker).upper(),)
        ).fetchone()
        con.close()
        if not row:
            return None,None
        op=float(row[0]) if row[0] is not None else None
        cur=float(row[1]) if row[1] is not None else op
        return op,cur
    except Exception:
        return None,None


def update_opening_confirmation():
    """
    Evaluate the immutable Pre-Market picks against the actual opening/current price.
    Does not rewrite the Pre-Market list.
    """
    _ensure_opening_tables()
    market_date=_market_date_wib()

    con=dbcon()
    try:
        picks=pd.read_sql_query(
            """SELECT market_date,rank,ticker,company,reference_price,
                      entry_low,entry_high,stop_loss,target_1,target_2
               FROM premarket_picks
               WHERE market_date=?
               ORDER BY rank""",
            con, params=(market_date,)
        )
    except Exception:
        con.close()
        return pd.DataFrame()
    con.close()

    if picks.empty:
        return pd.DataFrame()

    rows=[]
    now=datetime.now(timezone.utc).isoformat()
    dont_chase_gap=float(CFG.get('opening_dont_chase_gap_pct',3.0))
    cancel_gap=float(CFG.get('opening_cancel_gap_pct',-4.0))
    cancel_below_entry=float(CFG.get('opening_cancel_below_entry_pct',-2.0))

    for _,r in picks.iterrows():
        ticker=str(r.ticker)
        op,cur=_latest_intraday_open(ticker)
        if op is None:
            op,cur=_daily_open_fallback(ticker)
        if op is None:
            status='WAIT'
            reason='Opening price belum tersedia.'
            gap=None
        else:
            ref=float(r.reference_price) if pd.notna(r.reference_price) else (
                float(r.entry_high) if pd.notna(r.entry_high) else op
            )
            lo=float(r.entry_low) if pd.notna(r.entry_low) else ref
            hi=float(r.entry_high) if pd.notna(r.entry_high) else ref
            gap=((op/ref)-1)*100 if ref else 0.0

            if gap <= cancel_gap:
                status='CANCEL'
                reason=f'Gap turun {gap:+.2f}% melewati batas pembatalan.'
            elif op < lo*(1+cancel_below_entry/100):
                status='CANCEL'
                reason=f'Open {op:,.2f} terlalu jauh di bawah entry zone.'
            elif op > hi:
                chase=((op/hi)-1)*100 if hi else 0.0
                if gap >= dont_chase_gap or chase >= 1.0:
                    status="DON'T CHASE"
                    reason=f'Open {op:,.2f} di atas entry high {hi:,.2f}; hindari mengejar harga.'
                else:
                    status='WAIT'
                    reason=f'Open sedikit di atas entry zone; tunggu pullback/konfirmasi.'
            elif lo <= op <= hi:
                status='BUY'
                reason=f'Open {op:,.2f} masih di dalam entry zone {lo:,.2f}-{hi:,.2f}.'
            else:
                status='STILL STRONG'
                reason=f'Open {op:,.2f} masih dekat setup; pantau konfirmasi sebelum entry.'

        rows.append({
            'market_date':market_date,
            'ticker':ticker,
            'generated_at':now,
            'premarket_rank':int(r['rank']) if pd.notna(r['rank']) else None,
            'company':str(r['company']),
            'reference_price':float(r['reference_price']) if pd.notna(r['reference_price']) else None,
            'open_price':op,
            'current_price':cur,
            'entry_low':float(r['entry_low']) if pd.notna(r['entry_low']) else None,
            'entry_high':float(r['entry_high']) if pd.notna(r['entry_high']) else None,
            'gap_pct':gap,
            'status':status,
            'reason':reason
        })

    out=pd.DataFrame(rows)
    con=dbcon()
    try:
        for _,r in out.iterrows():
            con.execute("""
                INSERT OR REPLACE INTO opening_confirmation
                (market_date,ticker,generated_at,premarket_rank,company,
                 reference_price,open_price,current_price,entry_low,entry_high,
                 gap_pct,status,reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(
                r.market_date,r.ticker,r.generated_at,
                int(r.premarket_rank) if pd.notna(r.premarket_rank) else None,
                r.company,r.reference_price,r.open_price,r.current_price,
                r.entry_low,r.entry_high,r.gap_pct,r.status,r.reason
            ))
        con.commit()
    finally:
        con.close()

    try:
        app.state_set('opening_confirmation_at',now)
        app.state_set('opening_confirmation_count',len(out))
    except Exception:
        pass
    return out

def update(res, phase='CLOSED'):
    ensure_tables()
    if res is None or res.empty:
        return pd.DataFrame()

    prev=read_current()
    prev_map={str(r.ticker):r for _,r in prev.iterrows()} if not prev.empty else {}

    # Candidate pool: deliberately wider than BUY. The engine must be able to
    # discover a future opportunity before the strict decision gate becomes BUY.
    c=res.copy()
    c=c[~c.decision.isin(['SELL/AVOID'])].copy()
    c=c[pd.to_numeric(c.ai_score,errors='coerce').fillna(0)>=45]
    c=c[pd.to_numeric(c.relative_percentile,errors='coerce').fillna(0)>=0.70]
    if c.empty:
        return pd.DataFrame()
    c=c.sort_values(['relative_rank_score','ai_score'],ascending=False).head(30).reset_index(drop=True)

    rows=[]
    for _,r in c.iterrows():
        ts=_cached_time_signal(r.ticker)
        score=_score(r,ts)
        lo,hi=_dynamic_entry(r,ts)
        market_regime,market_score,ihsg_alignment,regime_adjustment=_market_context(r)
        prev_r=prev_map.get(str(r.ticker))
        prev_rank=int(prev_r['rank']) if prev_r is not None and pd.notna(prev_r['rank']) else None
        prev_score=float(prev_r['opportunity_score']) if prev_r is not None and pd.notna(prev_r['opportunity_score']) else None
        stable=int(prev_r['stable_cycles'])+1 if prev_r is not None and abs(score-(prev_score or score))<3.0 else 1
        rows.append({
            'ticker':str(r.ticker),'company':str(r.company),'score':score,'time':ts,
            'close':float(r.close),'entry_low':lo,'entry_high':hi,
            'stop_loss':float(r.stop_loss) if pd.notna(r.stop_loss) else np.nan,
            'target_1':float(r.target_1) if pd.notna(r.target_1) else np.nan,
            'target_2':float(r.target_2) if pd.notna(r.target_2) else np.nan,
            'risk_reward':float(r.risk_reward_2) if pd.notna(r.risk_reward_2) else np.nan,
            'risk':str(r.risk),'ai_score':float(r.ai_score),'p_up':float(r.jst_p_up) if pd.notna(r.jst_p_up) else np.nan,
            'p_down':float(r.jst_p_down) if pd.notna(r.jst_p_down) else np.nan,
            'confidence':float(r.jst_confidence) if pd.notna(r.jst_confidence) else np.nan,
            'relative_percentile':float(r.relative_percentile) if pd.notna(r.relative_percentile) else np.nan,
            'relative_rank_score':float(r.relative_rank_score) if pd.notna(r.relative_rank_score) else np.nan,
            'decision':str(r.decision),'decision_reason':str(r.decision_reason),
            'market_regime':market_regime,'market_score':market_score,'ihsg_alignment':ihsg_alignment,'regime_adjustment':regime_adjustment,
            'prev_rank':prev_rank,'prev_score':prev_score,'stable':stable
        })

    cand=pd.DataFrame(rows).sort_values('score',ascending=False).reset_index(drop=True)
    # Hysteresis: preserve an existing top opportunity when it remains viable and
    # a challenger is not materially stronger. This prevents Top-5 churn.
    top_n=int(CFG.get('dynamic_opportunity_count',5))
    replace_margin=float(CFG.get('dynamic_replace_margin',5.0))
    selected=[]
    selected_t=set()
    old_top=prev.sort_values('rank').head(top_n)['ticker'].astype(str).tolist() if not prev.empty else []
    for ticker in old_top:
        rr=cand[cand.ticker.astype(str)==ticker]
        if rr.empty: continue
        row=rr.iloc[0]
        if len(selected)<top_n:
            best_score=float(cand.iloc[0].score)
            if float(row.score) >= best_score-replace_margin:
                selected.append(row); selected_t.add(ticker)
    for _,row in cand.iterrows():
        if len(selected)>=top_n: break
        if str(row.ticker) in selected_t: continue
        selected.append(row); selected_t.add(str(row.ticker))
    selected=pd.DataFrame(selected)
    selected=selected.sort_values('score',ascending=False).reset_index(drop=True)

    now=datetime.now(timezone.utc).isoformat()
    con=dbcon(); con.execute('DELETE FROM dynamic_opportunities')
    con.execute('DELETE FROM dynamic_opportunity_pool')
    # Persist the monitored candidate pool, not only Top-5. Smart Alerts can
    # therefore detect a strong opportunity that appears at rank #6, #20, #37,
    # etc. without forcing every candidate into the user-facing Top-5.
    for pool_rank,pr in cand.iterrows():
        pts=pr.time
        pool_state=_state(pr,pts,float(pr.score))
        con.execute('''INSERT OR REPLACE INTO dynamic_opportunity_pool
            (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,decision)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (str(pr.ticker),str(pr.company),now,int(pool_rank)+1,float(pr.score),pool_state,
             str(pts.get('entry_status','NEUTRAL')),str(pts.get('horizon','EOD')),float(pr.close),str(pr.risk),
             float(pr.ai_score),float(pr.p_up) if pd.notna(pr.p_up) else None,float(pr.p_down) if pd.notna(pr.p_down) else None,
             float(pr.confidence) if pd.notna(pr.confidence) else None,
             float(pr.relative_percentile) if pd.notna(pr.relative_percentile) else None,
             float(pr.relative_rank_score) if pd.notna(pr.relative_rank_score) else None,
             float(pts.get('edge',0.0)),float(pts.get('confidence',0.0)),str(pr.decision)))
    out=[]
    for i,rr in selected.iterrows():
        rank=i+1; ts=rr.time
        state=_state(rr,ts,float(rr.score))
        reason=f"score {rr.score:.1f}; IHSG {rr.market_regime} {rr.market_score:.1f}; alignment {rr.ihsg_alignment:.1f}; regime adj {rr.regime_adjustment:+.1f}; relative {rr.relative_percentile*100:.1f}%; time edge {ts.get('edge',0)*100:+.1f}%; {rr.decision}"
        prev_rank=rr.prev_rank
        prev_rank_missing = prev_rank is None or pd.isna(prev_rank)
        replacement='NEW' if prev_rank_missing else ('RETAINED' if int(prev_rank)==rank else f'RANK_CHANGED {int(prev_rank)}→{rank}')
        vals=(rr.ticker,rr.company,now,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),str(ts.get('horizon','EOD')),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.target_2,rr.risk_reward,rr.risk,rr.ai_score,rr.p_up,rr.p_down,rr.confidence,rr.relative_percentile,rr.relative_rank_score,float(ts.get('edge',0.0)),float(ts.get('confidence',0.0)),reason,replacement,int(rr.stable),(None if prev_rank_missing else int(prev_rank)),rr.prev_score,rr.market_regime,rr.market_score,rr.ihsg_alignment,rr.regime_adjustment)
        con.execute('''INSERT OR REPLACE INTO dynamic_opportunities (ticker,company,generated_at,rank,opportunity_score,state,entry_status,horizon,close,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,ai_score,p_up,p_down,confidence,relative_percentile,relative_rank_score,time_edge,time_confidence,reason,replacement_reason,stable_cycles,previous_rank,previous_score,market_regime,market_score,ihsg_alignment,regime_adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)
        con.execute('INSERT INTO dynamic_opportunity_history(generated_at,ticker,rank,opportunity_score,state,entry_status,close,entry_low,entry_high,stop_loss,target_1,risk_reward,risk,decision,relative_percentile,time_edge) VALUES('+','.join(['?']*16)+')',(now,rr.ticker,rank,float(rr.score),state,str(ts.get('entry_status','NEUTRAL')),rr.close,rr.entry_low,rr.entry_high,rr.stop_loss,rr.target_1,rr.risk_reward,rr.risk,rr.decision,rr.relative_percentile,float(ts.get('edge',0.0))))
        out.append({'rank':rank,'ticker':rr.ticker,'company':rr.company,'opportunity_score':rr.score,'state':state,'entry_status':str(ts.get('entry_status','NEUTRAL')),'horizon':str(ts.get('horizon','EOD')),'close':rr.close,'entry_low':rr.entry_low,'entry_high':rr.entry_high,'stop_loss':rr.stop_loss,'target_1':rr.target_1,'target_2':rr.target_2,'risk_reward':rr.risk_reward,'risk':rr.risk,'ai_score':rr.ai_score,'p_up':rr.p_up,'p_down':rr.p_down,'confidence':rr.confidence,'relative_percentile':rr.relative_percentile,'time_edge':float(ts.get('edge',0.0)),'time_confidence':float(ts.get('confidence',0.0)),'reason':reason,'replacement_reason':replacement,'stable_cycles':int(rr.stable),'market_regime':rr.market_regime,'market_score':rr.market_score,'ihsg_alignment':rr.ihsg_alignment,'regime_adjustment':rr.regime_adjustment})
    con.commit(); con.close()
    app.state_set('dynamic_opportunity_at',datetime.now().isoformat())
    app.state_set('dynamic_opportunity_phase',phase)
    app.state_set('dynamic_opportunity_count',len(out))

    # Stage 3: only evaluates today's immutable Pre-Market list.
    # It never mutates the locked picks themselves.
    try:
        update_opening_confirmation()
    except Exception:
        pass

    return pd.DataFrame(out)
