import os, json, time, sqlite3, traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault('STREAMLIT_SERVER_HEADLESS','true')
os.environ['AI_ENGINE_WORKER']='1'
import app

BASE=app.BASE
CFG=app.CFGD
DB_DIR=app.DB_DIR


def dbcon():
    return sqlite3.connect(app.DB, check_same_thread=False)


def ensure_tables():
    con=dbcon()
    con.execute('''CREATE TABLE IF NOT EXISTS opportunities (
        ticker TEXT PRIMARY KEY, company TEXT, asof TEXT, close REAL, change_1d REAL,
        ai_score REAL, market_score REAL, jst_p_up REAL, jst_p_sideways REAL, jst_p_down REAL,
        jst_signal TEXT, jst_confidence REAL, trend TEXT, risk TEXT,
        stop_loss REAL, target_1 REAL, target_2 REAL, rank INTEGER, updated_at TEXT)''')
    con.execute('''CREATE TABLE IF NOT EXISTS engine_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phase TEXT, status TEXT, detail TEXT)''')
    con.commit(); con.close()


def log(phase,status,detail=''):
    now=datetime.now(timezone.utc).isoformat()
    con=dbcon(); con.execute('INSERT INTO engine_log(ts,phase,status,detail) VALUES(?,?,?,?)',(now,phase,status,str(detail)[:1000])); con.commit(); con.close()
    print(f'[{now}] {phase}: {status} {detail}', flush=True)


def get_last(key): return app.state_get(key)


def stale(key, hours):
    v=get_last(key)
    if not v: return True
    try:
        dt=datetime.fromisoformat(v.replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)-dt.astimezone(timezone.utc) > timedelta(hours=hours)
    except Exception: return True


def fetch_one(ticker):
    try:
        d=app.fetch_chart(ticker, CFG['daily_period_days'], CFG['daily_interval'])
        n=app.save_daily(d,ticker)
        return ticker, bool(not d.empty), n, ''
    except Exception as e:
        return ticker, False, 0, str(e)[:160]


def collect_fast(universe):
    tickers=universe.ticker.tolist(); ok=rows=0; errors=[]
    workers=max(4,min(int(CFG.get('collector_workers',12)),len(tickers)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures=[ex.submit(fetch_one,t) for t in tickers]
        for fut in as_completed(futures):
            t,good,n,err=fut.result(); ok+=int(good); rows+=n
            if err: errors.append(f'{t}: {err}')
    app.state_set('last_collection',datetime.now().isoformat())
    app.state_set('last_collection_ok',ok); app.state_set('last_collection_rows',rows); app.state_set('last_collection_errors',len(errors))
    return ok,rows,errors


def save_opportunities(res, source='technical-bootstrap'):
    if res is None or res.empty: return 0
    con=dbcon(); con.execute('DELETE FROM opportunities')
    now=datetime.now(timezone.utc).isoformat()
    for rank,(_,r) in enumerate(res.sort_values('ai_score',ascending=False).reset_index(drop=True).iterrows(),1):
        vals=(r.ticker,str(r.company),str(r.date),float(r.close),float(r.change_1d) if r.change_1d==r.change_1d else None,
            float(r.ai_score),float(r.market_score),float(r.jst_p_up) if r.jst_p_up==r.jst_p_up else None,
            float(r.jst_p_sideways) if r.jst_p_sideways==r.jst_p_sideways else None,float(r.jst_p_down) if r.jst_p_down==r.jst_p_down else None,
            str(r.jst_signal),float(r.jst_confidence) if r.jst_confidence==r.jst_confidence else None,str(r.trend),str(r.risk),
            float(r.stop_loss) if r.stop_loss==r.stop_loss else None,float(r.target_1) if r.target_1==r.target_1 else None,
            float(r.target_2) if r.target_2==r.target_2 else None,int(rank),now)
        con.execute('INSERT OR REPLACE INTO opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',vals)
    con.commit(); con.close()
    app.state_set('snapshot_ready_at',datetime.now().isoformat())
    app.state_set('snapshot_source',source)
    app.state_set('snapshot_count',len(res))
    return len(res)


def write_alerts(res):
    top=res.head(10).copy() if res is not None and not res.empty else res
    payload={'generated_at':datetime.now(timezone.utc).isoformat(),'count':0,'opportunities':[]}
    if top is not None and not top.empty:
        payload['count']=len(top)
        for _,r in top.iterrows():
            payload['opportunities'].append({'ticker':str(r.ticker),'company':str(r.company),'ai_score':round(float(r.ai_score),2),
                'p_up':None if r.jst_p_up!=r.jst_p_up else round(float(r.jst_p_up),4),'signal':str(r.jst_signal),'risk':str(r.risk),
                'trend':str(r.trend),'close':round(float(r.close),4),'stop_loss':None if r.stop_loss!=r.stop_loss else round(float(r.stop_loss),4),
                'target_1':None if r.target_1!=r.target_1 else round(float(r.target_1),4),'target_2':None if r.target_2!=r.target_2 else round(float(r.target_2),4)})
    path=os.path.join(DB_DIR,'latest_alerts.json')
    with open(path,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    app.state_set('last_alert_generation',datetime.now().isoformat())


def maybe_train(universe):
    hours=float(CFG.get('jst_retrain_hours',24))
    if not stale('jst_trained_at',hours): return 'SKIP'
    log('JST','START','background training; never blocks bootstrap snapshot')
    model,n,acc=app.train_jst(universe)
    if model:
        log('JST','DONE',f'rows={n} accuracy={acc}'); return 'DONE'
    log('JST','SKIP','insufficient data'); return 'SKIP'


def maybe_walk_forward(universe):
    days=float(CFG.get('walk_forward_days',7))
    if not stale('walk_forward_at',days*24): return 'SKIP'
    log('WALK_FORWARD','START','scheduled background model audit')
    out,mean=app.walk_forward_validation(universe,int(CFG.get('walk_forward_folds',2)))
    log('WALK_FORWARD','DONE',f'folds={len(out)} mean={mean}'); return 'DONE'


def prioritize_universe(universe):
    try:
        import pandas as pd
        seed=pd.read_csv(app.SEED); seed_t=set(seed.ticker.astype(str).str.upper())
        u=universe.copy(); u['ticker']=u.ticker.astype(str).str.upper()
        first=u[u.ticker.isin(seed_t)].copy(); rest=u[~u.ticker.isin(seed_t)].copy()
        return pd.concat([first,rest],ignore_index=True).drop_duplicates('ticker')
    except Exception: return universe


def cycle():
    ensure_tables()
    universe,source=app.load_universe(); universe=prioritize_universe(universe)
    log('ENGINE','START',f'universe={len(universe)} source={source}')

    # 1) FAST SNAPSHOT FIRST. No JST, no walk-forward, no full-universe wait.
    fast_n=min(int(CFG.get('bootstrap_symbols',30)),len(universe)); fast=universe.head(fast_n).copy()
    log('BOOTSTRAP','START',f'priority candidates={len(fast)}')
    ok,rows,errors=collect_fast(fast)
    log('BOOTSTRAP','COLLECT_DONE',f'ok={ok}/{len(fast)} rows={rows} errors={len(errors)}')
    base=app.analyze_all(fast,model=None)
    if not base.empty:
        save_opportunities(base,'technical-bootstrap'); write_alerts(base)
        log('BOOTSTRAP','SNAPSHOT_READY',f'analyzed={len(base)} top={base.iloc[0].ticker} score={base.iloc[0].ai_score:.2f}')
    else:
        log('BOOTSTRAP','SNAPSHOT_WAIT','no priority symbol has enough cached history yet')

    # 2) Background maintenance. JST can improve the snapshot without blocking the user.
    maybe_train(fast)
    model=app.load_jst()
    if model:
        refined=app.analyze_all(fast,model)
        if not refined.empty:
            save_opportunities(refined,'jst-refined-bootstrap'); write_alerts(refined)
            log('JST_REFINE','SNAPSHOT_UPDATED',f'analyzed={len(refined)}')

    # 3) Full universe runs in the background. The existing snapshot remains visible.
    log('FULL_COLLECT','START',f'background refresh {len(universe)} symbols')
    ok,rows,errors=collect_fast(universe)
    log('FULL_COLLECT','DONE',f'ok={ok}/{len(universe)} rows={rows} errors={len(errors)}')

    maybe_train(universe); maybe_walk_forward(universe)
    model=app.load_jst(); res=app.analyze_all(universe,model)
    if not res.empty:
        save_opportunities(res,'full-universe'); write_alerts(res)
        log('SCAN','DONE',f'analyzed={len(res)} top={res.iloc[0].ticker} score={res.iloc[0].ai_score:.2f}')
    else: log('SCAN','DONE','no symbols with sufficient data')
    app.state_set('engine_last_run',datetime.now().isoformat())
    return res


if __name__=='__main__':
    interval=max(5,int(CFG.get('worker_cycle_minutes',30)))*60
    print('AI Stock Intelligence V1.7 Snapshot First background engine started.',flush=True)
    while True:
        started=time.time()
        try: cycle()
        except Exception:
            traceback.print_exc(); log('ENGINE','ERROR',traceback.format_exc()[-900:])
        elapsed=time.time()-started; sleep_for=max(30,interval-elapsed)
        print(f'Next engine cycle in {sleep_for/60:.1f} min.',flush=True); time.sleep(sleep_for)
