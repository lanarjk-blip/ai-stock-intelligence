import json, os, sqlite3, time, math
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests
import streamlit as st

BASE=os.path.dirname(os.path.abspath(__file__))
WORKER_MODE=os.environ.get('AI_ENGINE_WORKER')=='1'
def cache_data(ttl):
    return (lambda f: f) if WORKER_MODE else (lambda f: st.cache_data(ttl=ttl)(f))
DB_DIR=os.path.join(BASE,'db'); os.makedirs(DB_DIR, exist_ok=True)
DB=os.path.join(DB_DIR,'market.db'); CFG=os.path.join(BASE,'config.json'); SEED=os.path.join(BASE,'data','universe_seed.csv')
with open(CFG,'r',encoding='utf-8') as f: CFGD=json.load(f)
FEATURES=['ret1','ret5','rsi14','ma20_gap','ma50_gap','macd_gap','atr_pct','vol_ratio','range_pct','ihsg_ret1','ihsg_ret5','ihsg_ma20_gap','rel_ret1','rel_ret5']
CLASS_NAMES=['DOWN','SIDEWAYS','UP']

def dbcon(): return sqlite3.connect(DB,check_same_thread=False)
def init_db():
    con=dbcon()
    con.execute('CREATE TABLE IF NOT EXISTS prices (ticker TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,updated_at TEXT,PRIMARY KEY(ticker,date))')
    con.execute('CREATE TABLE IF NOT EXISTS intraday (ticker TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,updated_at TEXT,PRIMARY KEY(ticker,ts))')
    con.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS market_index (symbol TEXT,date TEXT,close REAL,change_pct REAL,source TEXT,updated_at TEXT,PRIMARY KEY(symbol,date))')
    con.commit(); con.close()
init_db()

@cache_data(3600)
def load_universe():
    url=CFGD.get('universe_url','')
    if url:
        try:
            r=requests.get(url,timeout=10,headers={'User-Agent':CFGD['user_agent']}); r.raise_for_status()
            df=pd.read_csv(pd.io.common.BytesIO(r.content)); cols={c.lower():c for c in df.columns}
            tc=cols.get('ticker') or cols.get('code') or cols.get('symbol')
            if tc:
                out=pd.DataFrame({'ticker':df[tc].astype(str).str.upper().str.replace('.JK','',regex=False)})
                cc=cols.get('company') or cols.get('name') or cols.get('longname'); out['company']=df[cc].astype(str) if cc else out.ticker
                out=out[out.ticker.str.match(r'^[A-Z0-9]{2,6}$')].drop_duplicates('ticker')
                if len(out)>=100:return out,'remote universe'
        except Exception: pass
    return pd.read_csv(SEED),'bundled seed universe'

def state_get(k):
    con=dbcon(); r=con.execute('SELECT value FROM state WHERE key=?',(k,)).fetchone(); con.close(); return r[0] if r else None
def state_set(k,v):
    con=dbcon(); con.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)',(k,str(v))); con.commit(); con.close()

@cache_data(300)
def fetch_ihsg():
    end=int(time.time()); start=end-int(120*86400)
    url='https://query1.finance.yahoo.com/v8/finance/chart/%5EJKSE'
    params={'period1':start,'period2':end,'interval':'1d','events':'history','includeAdjustedClose':'true'}
    try:
        r=requests.get(url,params=params,timeout=CFGD['request_timeout_seconds'],headers={'User-Agent':CFGD['user_agent']}); r.raise_for_status()
        result=(r.json().get('chart') or {}).get('result') or []
        if not result:return pd.DataFrame()
        j=result[0]; ts=j.get('timestamp',[]); q=j['indicators']['quote'][0]
        if not ts:return pd.DataFrame()
        d=pd.DataFrame({'date':pd.to_datetime(ts,unit='s',utc=True).date,'close':q.get('close')}).dropna(subset=['close'])
        d['change_pct']=d.close.pct_change(fill_method=None)*100
        return d
    except Exception:
        return pd.DataFrame()

def save_ihsg(df):
    if df.empty:return 0
    con=dbcon(); n=0
    for _,r in df.iterrows():
        con.execute('INSERT OR REPLACE INTO market_index VALUES(?,?,?,?,?,?)',('IHSG',str(r.date),float(r.close),float(r.change_pct) if pd.notna(r.change_pct) else None,'Yahoo Finance',datetime.now(timezone.utc).isoformat())); n+=1
    con.commit(); con.close(); return n

def ihsg_snapshot():
    d=fetch_ihsg()
    if d.empty:return None
    save_ihsg(d)
    r=d.iloc[-1]; close=float(r.close); ch=float(r.change_pct) if pd.notna(r.change_pct) else np.nan
    ma20=float(d.close.tail(20).mean()) if len(d)>=20 else np.nan
    trend='BULLISH' if pd.notna(ma20) and close>ma20 else ('BEARISH' if pd.notna(ma20) else 'N/A')
    return {'close':close,'change_pct':ch,'ma20':ma20,'trend':trend,'date':str(r.date)}

@cache_data(120)
def fetch_chart(ticker, period_days, interval):
    end=int(time.time()); start=end-int(period_days*86400)
    url=f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.JK'
    params={'period1':start,'period2':end,'interval':interval,'events':'history','includeAdjustedClose':'true'}
    r=requests.get(url,params=params,timeout=CFGD['request_timeout_seconds'],headers={'User-Agent':CFGD['user_agent']}); r.raise_for_status()
    result=(r.json().get('chart') or {}).get('result') or []
    if not result:return pd.DataFrame()
    j=result[0]; ts=j.get('timestamp',[]); q=j['indicators']['quote'][0]
    if not ts:return pd.DataFrame()
    return pd.DataFrame({'ts':pd.to_datetime(ts,unit='s',utc=True),'open':q.get('open'),'high':q.get('high'),'low':q.get('low'),'close':q.get('close'),'volume':q.get('volume')}).dropna(subset=['close'])

def save_daily(df,ticker):
    if df.empty:return 0
    con=dbcon(); n=0
    for _,r in df.iterrows():
        con.execute('INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?,?,?,?,?)',(ticker,str(pd.Timestamp(r.ts).date()),r.open,r.high,r.low,r.close,r.volume,'Yahoo Finance chart',datetime.now(timezone.utc).isoformat())); n+=1
    con.commit(); con.close(); return n

def save_intraday(df,ticker):
    if df.empty:return 0
    con=dbcon(); n=0
    for _,r in df.iterrows():
        con.execute('INSERT OR REPLACE INTO intraday VALUES(?,?,?,?,?,?,?,?,?)',(ticker,pd.Timestamp(r.ts).isoformat(),r.open,r.high,r.low,r.close,r.volume,'Yahoo Finance 30m chart',datetime.now(timezone.utc).isoformat())); n+=1
    con.commit(); con.close(); return n

def collect_daily(universe,limit):
    ok=rows=0; errors=[]
    for t in universe.ticker.head(limit):
        try:
            d=fetch_chart(t,CFGD['daily_period_days'],CFGD['daily_interval']); rows+=save_daily(d,t); ok+=bool(not d.empty)
        except Exception as e: errors.append(f'{t}: {str(e)[:80]}')
        time.sleep(0.12)
    state_set('last_collection',datetime.now().isoformat()); return int(ok),rows,errors

def collect_intraday(tickers):
    ok=rows=0; errors=[]
    for t in tickers:
        try:
            d=fetch_chart(t,CFGD['intraday_period_days'],CFGD['intraday_interval']); rows+=save_intraday(d,t); ok+=bool(not d.empty)
        except Exception as e: errors.append(f'{t}: {str(e)[:80]}')
        time.sleep(0.12)
    state_set('last_intraday_collection',datetime.now().isoformat()); return int(ok),rows,errors

def indicators(df):
    x=df.sort_values('date').copy(); c=x.close; v=x.volume.fillna(0)
    x['ret1']=c.pct_change(fill_method=None); x['ret5']=c.pct_change(5); x['ma20']=c.rolling(20).mean(); x['ma50']=c.rolling(50).mean()
    x['ma20_gap']=c/x.ma20-1; x['ma50_gap']=c/x.ma50-1
    delta=c.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean(); rs=gain/loss.replace(0,np.nan)
    x['rsi14']=100-(100/(1+rs)); ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean(); x['macd']=ema12-ema26; x['macd_signal']=x.macd.ewm(span=9,adjust=False).mean(); x['macd_gap']=(x.macd-x.macd_signal)/c
    tr=pd.concat([(x.high-x.low),(x.high-c.shift()).abs(),(x.low-c.shift()).abs()],axis=1).max(axis=1); x['atr14']=tr.rolling(14).mean(); x['atr_pct']=x.atr14/c
    x['vol20']=v.rolling(20).mean(); x['vol_ratio']=v/x.vol20.replace(0,np.nan); x['range_pct']=(x.high-x.low)/c
    return x

def load_ihsg_daily(con):
    d=pd.read_sql_query("SELECT date,close,change_pct FROM market_index WHERE symbol='IHSG' ORDER BY date",con)
    if d.empty:return d
    d['date']=d['date'].astype(str)
    d['ihsg_ret1']=d['close'].pct_change(fill_method=None)
    d['ihsg_ret5']=d['close'].pct_change(5)
    d['ihsg_ma20']=d['close'].rolling(20).mean()
    d['ihsg_ma20_gap']=d['close']/d['ihsg_ma20']-1
    return d[['date','ihsg_ret1','ihsg_ret5','ihsg_ma20_gap']]

def build_training_rows(con,tickers):
    rows=[]
    ih=load_ihsg_daily(con)
    for t in tickers:
        df=pd.read_sql_query('SELECT date,open,high,low,close,volume FROM prices WHERE ticker=? ORDER BY date',con,params=(t,))
        if len(df)<80: continue
        z=indicators(df); z['date']=z['date'].astype(str)
        z=z.merge(ih[['date','ihsg_ret1','ihsg_ret5','ihsg_ma20_gap']],on='date',how='left')
        z['rel_ret1']=z.ret1-z.ihsg_ret1; z['rel_ret5']=z.ret5-z.ihsg_ret5
        z['future_ret1']=z.close.shift(-1)/z.close-1; z['future_ret3']=z.close.shift(-3)/z.close-1; z['future_ret5']=z.close.shift(-5)/z.close-1
        z=z.dropna(subset=FEATURES+['future_ret1','future_ret3','future_ret5'])
        for _,r in z.iterrows():
            f=r[FEATURES].astype(float).values
            if not np.all(np.isfinite(f)): continue
            yv=r.future_ret1; th=CFGD['future_threshold']; y=2 if yv>th else (0 if yv<-th else 1)
            rows.append((t,str(r.date),*f,int(y),float(r.future_ret1),float(r.future_ret3),float(r.future_ret5)))
    cols=['ticker','date']+FEATURES+['y','fret1','fret3','fret5']; return pd.DataFrame(rows,columns=cols)

class NumpyJST:
    def __init__(self,d,h=16,c=3,seed=42):
        rng=np.random.default_rng(seed); self.W1=rng.normal(0,np.sqrt(2/d),(d,h)); self.b1=np.zeros(h); self.W2=rng.normal(0,np.sqrt(2/h),(h,c)); self.b2=np.zeros(c); self.mu=np.zeros(d); self.sd=np.ones(d)
    def softmax(self,z):
        z=z-z.max(axis=1,keepdims=True); e=np.exp(z); return e/e.sum(axis=1,keepdims=True)
    def fit(self,X,y,epochs=8,lr=.01,batch=512):
        self.mu=X.mean(0); self.sd=X.std(0); self.sd[self.sd<1e-8]=1; X=(X-self.mu)/self.sd; n=len(X); rng=np.random.default_rng(7)
        for _ in range(epochs):
            idx=rng.permutation(n)
            for s in range(0,n,batch):
                ii=idx[s:s+batch]; xb=X[ii]; yb=y[ii]; h=np.tanh(xb@self.W1+self.b1); p=self.softmax(h@self.W2+self.b2); one=np.zeros_like(p); one[np.arange(len(yb)),yb]=1
                dz2=(p-one)/len(yb); dW2=h.T@dz2; db2=dz2.sum(0); dh=(dz2@self.W2.T)*(1-h*h); dW1=xb.T@dh; db1=dh.sum(0)
                self.W2-=lr*dW2; self.b2-=lr*db2; self.W1-=lr*dW1; self.b1-=lr*db1
    def predict_proba(self,X):
        X=(np.asarray(X)-self.mu)/self.sd; return self.softmax(np.tanh(X@self.W1+self.b1)@self.W2+self.b2)
    def accuracy(self,X,y): return float((self.predict_proba(X).argmax(1)==y).mean())

def train_jst(universe):
    con=dbcon(); tr=build_training_rows(con,universe.ticker.tolist()); con.close()
    if len(tr)<500:return None,0,None
    tr=tr.sort_values('date'); cut=max(1,int(len(tr)*0.8)); train=tr.iloc[:cut]; test=tr.iloc[cut:]
    X=train[FEATURES].values.astype(float); y=train.y.values.astype(int); Xt=test[FEATURES].values.astype(float); yt=test.y.values.astype(int)
    model=NumpyJST(len(FEATURES),CFGD['jst_hidden']); model.fit(X,y,CFGD['jst_epochs'],CFGD['jst_learning_rate'],CFGD['jst_batch_size']); acc=model.accuracy(Xt,yt) if len(test) else None
    np.savez(os.path.join(DB_DIR,'jst_model.npz'),W1=model.W1,b1=model.b1,W2=model.W2,b2=model.b2,mu=model.mu,sd=model.sd)
    state_set('jst_trained_at',datetime.now().isoformat()); state_set('jst_test_accuracy',acc if acc is not None else ''); state_set('jst_train_rows',len(tr)); return model,len(tr),acc

def walk_forward_validation(universe,folds=3):
    con=dbcon(); tr=build_training_rows(con,universe.ticker.tolist()); con.close()
    if len(tr)<1000:return pd.DataFrame(),None
    tr=tr.sort_values('date').reset_index(drop=True); max_rows=int(CFGD.get('validation_max_rows',150000))
    if len(tr)>max_rows: tr=tr.iloc[-max_rows:].reset_index(drop=True)
    n=len(tr); results=[]
    # expanding-window evaluation, strictly chronological
    for i in range(1,folds+1):
        train_end=int(n*(0.45+0.12*(i-1))); test_end=int(n*(0.57+0.12*(i-1)))
        if test_end<=train_end or train_end<300: continue
        train=tr.iloc[:train_end]; test=tr.iloc[train_end:test_end]
        m=NumpyJST(len(FEATURES),CFGD['jst_hidden'],seed=42+i); m.fit(train[FEATURES].values.astype(float),train.y.values.astype(int),max(3,int(CFGD['jst_epochs']//2)),CFGD['jst_learning_rate'],CFGD['jst_batch_size'])
        pred=m.predict_proba(test[FEATURES].values.astype(float)); yp=pred.argmax(1); yt=test.y.values.astype(int)
        acc=float((yp==yt).mean()); up_mask=yt==2; up_precision=float((yp[up_mask]==2).mean()) if up_mask.any() else np.nan
        results.append({'fold':i,'train_rows':len(train),'test_rows':len(test),'test_accuracy':acc,'up_recall':up_precision,'test_start':str(test.date.min()),'test_end':str(test.date.max())})
    out=pd.DataFrame(results); mean=float(out.test_accuracy.mean()) if not out.empty else None
    state_set('walk_forward_accuracy',mean if mean is not None else ''); state_set('walk_forward_folds',len(out)); state_set('walk_forward_at',datetime.now().isoformat()); return out,mean

def load_jst():
    p=os.path.join(DB_DIR,'jst_model.npz')
    if not os.path.exists(p):return None
    try:
        z=np.load(p); m=NumpyJST(len(FEATURES),CFGD['jst_hidden']); m.W1=z['W1'];m.b1=z['b1'];m.W2=z['W2'];m.b2=z['b2'];m.mu=z['mu'];m.sd=z['sd']; return m
    except Exception:return None

def analyze_all(universe,model=None):
    con=dbcon(); parts=[]
    ih=load_ihsg_daily(con)
    ih_latest=ih.iloc[-1].to_dict() if not ih.empty else {}
    ihsg_gap=float(ih_latest.get('ihsg_ma20_gap',0) or 0); ihsg_r5=float(ih_latest.get('ihsg_ret5',0) or 0); ihsg_r1=float(ih_latest.get('ihsg_ret1',0) or 0)
    market_score=float(np.clip(50+ihsg_gap*200+ihsg_r5*100,-0+0,100))
    for t in universe.ticker.tolist():
        df=pd.read_sql_query('SELECT * FROM prices WHERE ticker=? ORDER BY date',con,params=(t,))
        if len(df)<60:continue
        z=indicators(df); r=z.iloc[-1]; base=50
        base+=max(-10,min(10,(r.rsi14-50)*.25)) if pd.notna(r.rsi14) else 0; base+=(12 if r.ma20>r.ma50 else -12) if pd.notna(r.ma20) and pd.notna(r.ma50) else 0; base+=(10 if r.macd>r.macd_signal else -10) if pd.notna(r.macd) and pd.notna(r.macd_signal) else 0; base+=max(-5,min(5,(r.vol_ratio-1)*5)) if pd.notna(r.vol_ratio) else 0; base+=max(-8,min(8,r.ret5*100)) if pd.notna(r.ret5) else 0; base=float(np.clip(base,0,100))
        feat=dict(r); feat.update({'ihsg_ret1':ihsg_r1,'ihsg_ret5':ihsg_r5,'ihsg_ma20_gap':ihsg_gap,'rel_ret1':float(r.ret1)-ihsg_r1 if pd.notna(r.ret1) else np.nan,'rel_ret5':float(r.ret5)-ihsg_r5 if pd.notna(r.ret5) else np.nan})
        f=np.array([[feat[k] for k in FEATURES]],dtype=float); jst_p=model.predict_proba(f)[0] if model is not None and np.all(np.isfinite(f)) else np.array([np.nan,np.nan,np.nan])
        if np.all(np.isfinite(jst_p)): p_down,p_side,p_up=jst_p; jst_signal=CLASS_NAMES[int(jst_p.argmax())]; jst_conf=float(jst_p.max()); score=.45*base+.35*(p_up*100)+.20*market_score
        else:p_down=p_side=p_up=np.nan;jst_signal='N/A';jst_conf=np.nan;score=.80*base+.20*market_score
        trend='BULLISH' if pd.notna(r.ma20) and pd.notna(r.ma50) and r.ma20>r.ma50 else 'BEARISH'; atr=float(r.atr14) if pd.notna(r.atr14) else np.nan; close=float(r.close); risk=float(np.clip(45+(r.atr_pct*100*8 if pd.notna(r.atr_pct) else 0)+(8 if r.rsi14>75 or r.rsi14<25 else 0),0,100)); risk_label='LOW' if risk<40 else ('MEDIUM' if risk<65 else 'HIGH'); sl=close-1.5*atr if pd.notna(atr) else np.nan; tp1=close+1.5*atr if pd.notna(atr) else np.nan; tp2=close+2.5*atr if pd.notna(atr) else np.nan
        parts.append({'ticker':t,'company':universe.loc[universe.ticker==t,'company'].iloc[0],'date':r.date,'close':close,'change_1d':r.ret1,'rsi14':r.rsi14,'vol_ratio':r.vol_ratio,'trend':trend,'baseline_score':base,'jst_p_down':p_down,'jst_p_sideways':p_side,'jst_p_up':p_up,'jst_signal':jst_signal,'jst_confidence':jst_conf,'market_score':market_score,'ai_score':score,'risk':risk_label,'stop_loss':sl,'target_1':tp1,'target_2':tp2})
    con.close(); return pd.DataFrame(parts).sort_values('ai_score',ascending=False) if parts else pd.DataFrame()

def intraday_prediction(ticker):
    con=dbcon(); df=pd.read_sql_query('SELECT ts,open,high,low,close,volume FROM intraday WHERE ticker=? ORDER BY ts',con,params=(ticker,)); con.close()
    if len(df)<60:return pd.DataFrame()
    x=df.copy(); x['ts']=pd.to_datetime(x.ts,utc=True); x['wib_hour']=(x.ts.dt.hour+7)%24; x['wib_min']=x.ts.dt.minute; x['tod']=x.wib_hour*60+x.wib_min
    x['ret1']=x.close.pct_change(fill_method=None); x['vol20']=x.volume.rolling(20).mean(); x['vol_ratio']=x.volume/x.vol20.replace(0,np.nan); x['ret30']=x.close.shift(-1)/x.close-1; x['ret60']=x.close.shift(-2)/x.close-1
    x['day']=x.ts.dt.date; x['day_end']=x.groupby('day').close.transform('last'); x['reteod']=x.day_end/x.close-1
    x=x.dropna(subset=['ret30','ret60','reteod','vol_ratio'])
    now=df.iloc[-1]; now_ts=pd.to_datetime(now.ts,utc=True); now_hour=(now_ts.hour+7)%24; now_min=now_ts.minute; now_tod=now_hour*60+now_min
    # Match nearby time-of-day plus similar short-term direction and volume regime.
    x['tod_dist']=(x.tod-now_tod).abs(); pool=x[x.tod_dist<=60].copy()
    if len(pool)<20: pool=x.copy()
    current_ret=(df.close.iloc[-1]/df.close.iloc[-2]-1) if len(df)>1 else 0; current_vol=(df.volume.iloc[-1]/df.volume.tail(20).mean()) if df.volume.tail(20).mean()>0 else 1
    pool['sim']=np.abs(pool.ret1-current_ret)+0.25*np.abs(np.log(pool.vol_ratio.clip(lower=0.1))-np.log(max(current_vol,0.1)))+0.002*pool.tod_dist
    pool=pool.sort_values('sim').head(min(200,len(pool)))
    rows=[]
    for name,col in [('30m','ret30'),('60m','ret60'),('EOD','reteod')]:
        s=pool[col]; up=float((s>CFGD.get('time_return_threshold',0.002)).mean()); down=float((s<-CFGD.get('time_return_threshold',0.002)).mean()); side=max(0,1-up-down); mean=float(s.mean()); rows.append({'horizon':name,'samples':len(s),'p_up':up,'p_sideways':side,'p_down':down,'mean_return':mean})
    return pd.DataFrame(rows)

def read_opportunities(limit=100):
    try:
        con=dbcon(); q='SELECT * FROM opportunities ORDER BY rank LIMIT ?'; d=pd.read_sql_query(q,con,params=(int(limit),)); con.close(); return d
    except Exception:
        return pd.DataFrame()

def read_engine_log(limit=30):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT ts,phase,status,detail FROM engine_log ORDER BY id DESC LIMIT ?',con,params=(int(limit),)); con.close(); return d
    except Exception:
        return pd.DataFrame()

def _decision(row):
    """Simple decision layer used for the pre-market snapshot."""
    score=float(row.get('ai_score', 0) or 0)
    trend=str(row.get('trend',''))
    risk=str(row.get('risk','MEDIUM'))
    p_up=row.get('jst_p_up', np.nan)
    p_down=row.get('jst_p_down', np.nan)
    if pd.notna(p_up) and float(p_up)>=0.62 and score>=72 and trend=='BULLISH':
        return 'BUY SETUP'
    if pd.notna(p_down) and float(p_down)>=0.62 and score<=42:
        return 'SELL / AVOID'
    if score>=80 and trend=='BULLISH':
        return 'BUY WATCH'
    if score<=35 or (trend=='BEARISH' and score<50):
        return 'SELL / AVOID'
    return 'WAIT'


def _snapshot_status():
    snap=state_get('snapshot_ready_at')
    source=state_get('snapshot_source') or ''
    return snap,source


def render_app():
    st.set_page_config(page_title='AI Stock Intelligence V1.7 Snapshot First',layout='wide')
    st.title('AI Stock Intelligence V1.7 • Snapshot First')
    st.caption('AI menyiapkan hasil di background. Saat aplikasi dibuka, dashboard membaca snapshot terakhir terlebih dahulu.')
    universe,source=load_universe(); res=read_opportunities(100); model=load_jst()
    engine_last=state_get('engine_last_run'); trained=state_get('jst_trained_at'); acc=state_get('jst_test_accuracy'); wf=state_get('walk_forward_accuracy')
    snap_at,snap_source=_snapshot_status()
    ihsg=ihsg_snapshot()

    if not res.empty:
        res=res.copy(); res['decision']=res.apply(_decision,axis=1)

    c1,c2,c3,c4,c5=st.columns(5)
    c1.metric('Universe',len(universe),'auto')
    c2.metric('Intelligence','READY' if snap_at and not res.empty else 'PREPARING')
    c3.metric('Snapshot',snap_at or 'Belum ada')
    c4.metric('JST', 'TRAINED' if model else 'BACKGROUND')
    c5.metric('WF accuracy',f'{float(wf)*100:.1f}%' if wf else '—')

    if ihsg:
        st.subheader('Market Context • IHSG')
        m1,m2,m3,m4=st.columns(4)
        m1.metric('IHSG',f"{ihsg['close']:,.2f}")
        m2.metric('IHSG 1D',f"{ihsg['change_pct']:+.2f}%" if np.isfinite(ihsg['change_pct']) else '—')
        m3.metric('IHSG MA20',f"{ihsg['ma20']:,.2f}" if np.isfinite(ihsg['ma20']) else '—')
        m4.metric('Market Trend',ihsg['trend'])

    st.subheader('🔥 Market Intelligence')
    if res.empty:
        st.warning('Snapshot belum tersedia. Engine sedang membuat analisis awal. Setelah snapshot pertama tersimpan, pembukaan aplikasi berikutnya akan langsung menampilkan hasil terakhir.')
    else:
        buy=int(res.decision.isin(['BUY SETUP','BUY WATCH']).sum())
        sell=int((res.decision=='SELL / AVOID').sum())
        wait=int((res.decision=='WAIT').sum())
        a,b,c,d=st.columns(4)
        a.metric('🟢 BUY',buy); b.metric('🔴 SELL / AVOID',sell); c.metric('🟡 WAIT',wait); d.metric('Top 10',min(10,len(res)))
        top10=res.head(10).copy()
        cols=['rank','ticker','company','close','ai_score','decision','jst_p_up','jst_p_down','jst_signal','trend','risk','stop_loss','target_1','target_2']
        st.dataframe(top10[cols].round(3),width='stretch',hide_index=True)
        best=top10.iloc[0]
        st.success(f"🔥 Opportunity terkuat: **{best.ticker}** • {best.decision} • AI Score {best.ai_score:.1f} • Risk {best.risk}")
        st.caption(f"Snapshot: {snap_at or '—'} • Source: {snap_source or 'background engine'} • Probabilitas adalah estimasi model, bukan jaminan profit atau instruksi transaksi.")

    st.subheader('⚙️ Background Engine')
    s1,s2,s3=st.columns(3)
    s1.metric('Last engine cycle',engine_last or 'Belum ada')
    s2.metric('Last JST training',trained or 'Background / belum ada')
    s3.metric('Last walk-forward',state_get('walk_forward_at') or 'Scheduled / belum ada')
    st.info('Tidak ada tombol Scan Universe, Train JST, atau Walk-forward. Engine bekerja di background. Snapshot terakhir dipakai lebih dulu; model JST dan validasi berat tidak boleh menghalangi hasil awal.')

    with st.expander('Log proses AI'):
        logdf=read_engine_log(40)
        if logdf.empty: st.write('Belum ada log.')
        else: st.dataframe(logdf,width='stretch',hide_index=True)

    st.subheader('Semua Ranking AI')
    if not res.empty:
        st.dataframe(res.round(3),width='stretch',hide_index=True)

if __name__ == '__main__':
    render_app()
