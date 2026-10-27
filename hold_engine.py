import sqlite3
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import app
import market_session

CFG=app.CFGD

def dbcon(): return app.dbcon()

def ensure_tables():
    con=dbcon()
    con.execute('''CREATE TABLE IF NOT EXISTS hold_intelligence (
        ticker TEXT PRIMARY KEY, company TEXT, generated_at TEXT, phase TEXT,
        current_price REAL, state TEXT, hold_horizon TEXT, hold_score REAL,
        p_hold REAL, p_up REAL, p_down REAL, confidence REAL,
        time_edge REAL, risk TEXT, stop_loss REAL, target_1 REAL, target_2 REAL,
        risk_reward REAL, invalidation TEXT, overnight_state TEXT,
        next_session_bias TEXT, overnight_score REAL, reason TEXT,
        last_opportunity_score REAL, opportunity_rank INTEGER)''')
    existing={str(r[1]) for r in con.execute('PRAGMA table_info(hold_intelligence)').fetchall()}
    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_effect','REAL'),('price_source','TEXT'),('intraday_fresh','INTEGER'),('intraday_at','TEXT'),('time_status','TEXT')]:
        if col not in existing: con.execute(f'ALTER TABLE hold_intelligence ADD COLUMN {col} {typ}')
    con.execute('''CREATE TABLE IF NOT EXISTS hold_intelligence_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, ticker TEXT,
        phase TEXT, state TEXT, hold_horizon TEXT, hold_score REAL,
        overnight_state TEXT, next_session_bias TEXT, overnight_score REAL,
        current_price REAL, stop_loss REAL, target_1 REAL, risk_reward REAL)''')
    con.commit(); con.close()

def read_current():
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT * FROM hold_intelligence ORDER BY hold_score DESC',con); con.close(); return d
    except Exception: return pd.DataFrame()

def _daily_context(ticker):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT date,close,volume FROM prices WHERE ticker=? ORDER BY date',(str(ticker),)); con.close()
        if d.empty: return {'trend':'UNKNOWN','ret5':0.0,'vol_ratio':1.0}
        d['close']=pd.to_numeric(d.close,errors='coerce'); d['volume']=pd.to_numeric(d.volume,errors='coerce'); d=d.dropna(subset=['close'])
        ret5=float(d.close.iloc[-1]/d.close.iloc[-6]-1) if len(d)>=6 else 0.0
        ma20=float(d.close.tail(20).mean()) if len(d)>=20 else float(d.close.mean())
        ma50=float(d.close.tail(50).mean()) if len(d)>=50 else ma20
        trend='BULLISH' if d.close.iloc[-1]>ma20 and ma20>=ma50 else ('BEARISH' if d.close.iloc[-1]<ma20 and ma20<=ma50 else 'MIXED')
        v20=float(d.volume.tail(20).mean()) if d.volume.notna().any() else 0.0
        vr=float(d.volume.iloc[-1]/v20) if v20>0 and pd.notna(d.volume.iloc[-1]) else 1.0
        return {'trend':trend,'ret5':ret5,'vol_ratio':vr}
    except Exception: return {'trend':'UNKNOWN','ret5':0.0,'vol_ratio':1.0}

def _live_intraday_context(ticker, now=None):
    from live_evidence import read_intraday_context
    return read_intraday_context(dbcon,ticker,CFG,now)


def _time(ticker, now=None):
    live=_live_intraday_context(ticker,now=now)
    if live.get('live_required') and not live.get('fresh'):
        return {
            'edge':0.0,'confidence':0.0,'horizon':'EOD','status':'NEUTRAL',
            'live_required':True,'intraday_fresh':False,
            'intraday_at':str(live.get('ts') or ''),'price_source':'DAILY_CLOSE_STALE_INTRADAY',
            'live_price':None,
        }
    try:
        tp=app.intraday_prediction(str(ticker))
        if tp is None or tp.empty:
            edge=0.0; confidence=0.0; horizon='EOD'; status='NEUTRAL'
        else:
            r=tp[tp.horizon.astype(str)=='EOD']
            if r.empty:r=tp.iloc[[0]]
            r=r.iloc[0]
            edge=float(r.edge); confidence=float(r.confidence); horizon=str(r.horizon); status=str(r.entry_status)
        return {
            'edge':edge,'confidence':confidence,'horizon':horizon,'status':status,
            'live_required':bool(live.get('live_required')),'intraday_fresh':bool(live.get('fresh')),
            'intraday_at':str(live.get('ts') or ''),
            'price_source':'LIVE_INTRADAY' if live.get('fresh') else 'HISTORICAL_INTRADAY',
            'live_price':float(live['price']) if live.get('fresh') and live.get('price') is not None else None,
        }
    except Exception:
        return {
            'edge':0.0,'confidence':0.0,'horizon':'EOD','status':'NEUTRAL',
            'live_required':bool(live.get('live_required')),'intraday_fresh':bool(live.get('fresh')),
            'intraday_at':str(live.get('ts') or ''),
            'price_source':'LIVE_INTRADAY' if live.get('fresh') else ('DAILY_CLOSE_STALE_INTRADAY' if live.get('live_required') else 'HISTORICAL_INTRADAY'),
            'live_price':float(live['price']) if live.get('fresh') and live.get('price') is not None else None,
        }

def _ihsg_context():
    try:
        con=dbcon(); ih=app.load_ihsg_daily(con); con.close()
        regime,market_score=app.market_regime(ih)
        if ih is None or ih.empty: return regime,market_score,0.0,0.0
        latest=ih.iloc[-1]
        r1=float(latest.get('ihsg_ret1',0) or 0); r5=float(latest.get('ihsg_ret5',0) or 0); gap=float(latest.get('ihsg_ma20_gap',0) or 0)
        return regime,market_score,r1,r5+gap
    except Exception: return 'UNKNOWN',50.0,0.0,0.0

def _ihsg_effect(r, ctx):
    regime,market_score,_,r5_gap=ctx
    pup=float(r.jst_p_up or .5); pdown=float(r.jst_p_down or .5)
    stock_bias=(pup-pdown)
    alignment=float(np.clip(50+0.55*(market_score-50)+25*stock_bias,0,100))
    effect=float(np.clip((alignment-50)*0.18,-9,9))
    if regime=='HIGH_VOLATILITY': effect-=2.0
    return alignment,float(np.clip(effect,-11,9))

def _hold_score(r,ts,dc,market_ctx):
    ai=float(r.ai_score or 0); conf=float(r.jst_confidence or 0)*100
    pup=float(r.jst_p_up or .5)*100; pdown=float(r.jst_p_down or .5)*100
    rel=float(r.relative_percentile or 0)*100
    edge=float(ts['edge'])*100; tconf=float(ts['confidence'])*100
    trend=dc['trend']; trend_bonus=5 if trend=='BULLISH' else (-5 if trend=='BEARISH' else 0)
    alignment,regime_effect=_ihsg_effect(r,market_ctx)
    risk_pen={'LOW':0,'MEDIUM':4,'HIGH':9,'EXTREME':14}.get(str(r.risk or '').upper().strip(),6)
    persistence=0.42*pup+0.18*conf+0.14*rel+0.10*(50+edge)+0.08*tconf+0.08*alignment+trend_bonus+regime_effect-risk_pen
    return float(np.clip(persistence,0,100))

def _overnight(r,ts,dc,phase,market_ctx):
    pup=float(r.jst_p_up or .5); pdown=float(r.jst_p_down or .5); conf=float(r.jst_confidence or 0)
    rel=float(r.relative_percentile or 0); ai=float(r.ai_score or 0)
    edge=float(ts['edge']); risk=str(r.risk or '').upper().strip()
    score=50 + (pup-pdown)*35 + (ai-50)*0.25 + (rel-.5)*20 + edge*30
    if dc['trend']=='BULLISH': score+=4
    elif dc['trend']=='BEARISH': score-=4
    alignment,regime_effect=_ihsg_effect(r,market_ctx)
    score += regime_effect*0.9 + (alignment-50)*0.08
    if risk=='HIGH': score-=6
    score=float(np.clip(score,0,100))
    if score>=62 and pup>=pdown and conf>=0.40: state='VALID'
    elif score<=40 or pdown>pup+0.08: state='INVALIDATING'
    else: state='UNCERTAIN'
    if state=='VALID': bias='POSITIVE'
    elif state=='INVALIDATING': bias='NEGATIVE'
    else: bias='NEUTRAL'
    if phase in ('POST_CLOSE','OVERNIGHT','CLOSED'): label=state
    else: label='PENDING_CLOSE'
    return label,bias,score

def _state(score,r,overnight):
    dec=str(r.decision or '').upper().strip()
    if overnight=='INVALIDATING': return 'REDUCE WATCH'
    if score>=70 and dec in ('BUY','STRONG_BUY','NEAR ENTRY','WATCH'): return 'HOLD CANDIDATE'
    if score>=58: return 'HOLD / MONITOR'
    return 'REVIEW'

def _clear_current(status='NO_CANDIDATE'):
    con=dbcon()
    try:
        con.execute('DELETE FROM hold_intelligence')
        con.commit()
    finally:
        con.close()
    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat())
    app.state_set('hold_intelligence_count',0)
    app.state_set('hold_intelligence_status',status)


def update(res,phase='CLOSED'):
    ensure_tables()
    if res is None or res.empty:
        _clear_current('NO_DATA')
        return pd.DataFrame()
    c=res.copy(); decisions=c.decision.astype(str).str.upper().str.strip(); c=c[~decisions.isin({'SELL','SELL/AVOID','AVOID','STRONG_SELL','EXIT'})].copy()
    c=c[pd.to_numeric(c.ai_score,errors='coerce').fillna(0)>=45]
    c=c[pd.to_numeric(c.relative_percentile,errors='coerce').fillna(0)>=0.70]
    if c.empty:
        _clear_current('NO_CANDIDATE')
        return pd.DataFrame()
    c=c.sort_values(['relative_rank_score','ai_score'],ascending=False).head(int(CFG.get('hold_candidate_pool',20)))
    market_ctx=_ihsg_context()
    rows=[]
    for _,r in c.iterrows():
        ts=_time(r.ticker); dc=_daily_context(r.ticker); hs=_hold_score(r,ts,dc,market_ctx); ov, bias, os=_overnight(r,ts,dc,phase,market_ctx)
        current_price=float(ts['live_price']) if ts.get('live_price') is not None else float(r.close)
        ihsg_alignment,regime_effect=_ihsg_effect(r,market_ctx)
        state=_state(hs,r,ov)
        horizon='1-3D' if hs>=65 else ('NEXT_SESSION' if hs>=55 else 'EOD')
        live_note='intraday FRESH' if ts.get('intraday_fresh') else ('intraday STALE/NEUTRAL' if ts.get('live_required') else 'off-market/historical')
        reason=f'Hold score {hs:.1f}; P(UP) {float(r.jst_p_up)*100:.1f}%; P(DOWN) {float(r.jst_p_down)*100:.1f}%; time edge {ts["edge"]*100:+.1f}%; {live_note}; overnight {ov}.'
        rows.append({'ticker':str(r.ticker),'company':str(r.company),'current_price':current_price,'state':state,'hold_horizon':horizon,'hold_score':hs,'p_hold':min(0.95,max(0.05,hs/100.0)),'p_up':float(r.jst_p_up),'p_down':float(r.jst_p_down),'confidence':float(r.jst_confidence),'time_edge':float(ts['edge']),'risk':str(r.risk),'stop_loss':r.stop_loss,'target_1':r.target_1,'target_2':r.target_2,'risk_reward':r.risk_reward_2,'invalidation':r.invalidation,'overnight_state':ov,'next_session_bias':bias,'overnight_score':os,'reason':reason,'last_opportunity_score':float(r.ai_score),'opportunity_rank':int(r['rank']) if 'rank' in r.index and pd.notna(r['rank']) else None,
        'market_regime':market_ctx[0],'market_score':market_ctx[1],'ihsg_alignment':ihsg_alignment,'regime_effect':regime_effect,'price_source':str(ts.get('price_source') or 'DAILY_CLOSE'),'intraday_fresh':1 if ts.get('intraday_fresh') else 0,'intraday_at':str(ts.get('intraday_at') or ''),'time_status':str(ts.get('status') or 'NEUTRAL')})
    out=pd.DataFrame(rows).sort_values('hold_score',ascending=False).reset_index(drop=True)
    now=datetime.now(timezone.utc).isoformat(); con=dbcon(); con.execute('DELETE FROM hold_intelligence')
    for _,r in out.iterrows():
        con.execute('''INSERT OR REPLACE INTO hold_intelligence (ticker,company,generated_at,phase,current_price,state,hold_horizon,hold_score,p_hold,p_up,p_down,confidence,time_edge,risk,stop_loss,target_1,target_2,risk_reward,invalidation,overnight_state,next_session_bias,overnight_score,reason,last_opportunity_score,opportunity_rank,market_regime,market_score,ihsg_alignment,regime_effect,price_source,intraday_fresh,intraday_at,time_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(r.ticker,r.company,now,phase,r.current_price,r.state,r.hold_horizon,r.hold_score,r.p_hold,r.p_up,r.p_down,r.confidence,r.time_edge,r.risk,r.stop_loss,r.target_1,r.target_2,r.risk_reward,r.invalidation,r.overnight_state,r.next_session_bias,r.overnight_score,r.reason,r.last_opportunity_score,r.opportunity_rank,r.market_regime,r.market_score,r.ihsg_alignment,r.regime_effect,r.price_source,int(r.intraday_fresh),r.intraday_at,r.time_status))
        con.execute('''INSERT INTO hold_intelligence_history(generated_at,ticker,phase,state,hold_horizon,hold_score,overnight_state,next_session_bias,overnight_score,current_price,stop_loss,target_1,risk_reward) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(now,r.ticker,phase,r.state,r.hold_horizon,r.hold_score,r.overnight_state,r.next_session_bias,r.overnight_score,r.current_price,r.stop_loss,r.target_1,r.risk_reward))
    con.commit(); con.close()
    app.state_set('hold_intelligence_at',datetime.now(timezone.utc).isoformat()); app.state_set('hold_intelligence_phase',phase); app.state_set('hold_intelligence_count',len(out)); app.state_set('hold_intelligence_status','READY')
    return out
