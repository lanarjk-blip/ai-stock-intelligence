import json, os, sqlite3, time, math, re, hashlib, inspect
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests
import streamlit as st
import smart_alerts
import notification_engine
import recommendation_history

BASE=os.path.dirname(os.path.abspath(__file__))
WORKER_MODE=os.environ.get('AI_ENGINE_WORKER')=='1'
def cache_data(ttl):
    return (lambda f: f) if WORKER_MODE else (lambda f: st.cache_data(ttl=ttl)(f))
DB_DIR=os.path.join(BASE,'db'); os.makedirs(DB_DIR, exist_ok=True)
DB=os.path.join(DB_DIR,'market.db'); CFG=os.path.join(BASE,'config.json'); SEED=os.path.join(BASE,'data','universe_seed.csv')
with open(CFG,'r',encoding='utf-8') as f: CFGD=json.load(f)
FEATURES=['ret1','ret3','ret5','ret10','rsi14','rsi_slope','ma20_gap','ma50_gap','macd_gap','macd_slope','atr_pct','atr_pct_change','vol_ratio','vol_ratio_change','range_pct','high20_gap','low20_gap','trend_strength','ihsg_ret1','ihsg_ret5','ihsg_ret10','ihsg_ma20_gap','ihsg_vol20','rel_ret1','rel_ret5','rel_ret10']
CLASS_NAMES=['DOWN','SIDEWAYS','UP']

def dbcon():
    con=sqlite3.connect(DB,check_same_thread=False,timeout=30)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.execute('PRAGMA synchronous=NORMAL')
    return con
def init_db():
    con=dbcon()
    con.execute('CREATE TABLE IF NOT EXISTS prices (ticker TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,updated_at TEXT,PRIMARY KEY(ticker,date))')
    con.execute('CREATE TABLE IF NOT EXISTS intraday (ticker TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,updated_at TEXT,PRIMARY KEY(ticker,ts))')
    con.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS market_index (symbol TEXT,date TEXT,close REAL,change_pct REAL,source TEXT,updated_at TEXT,PRIMARY KEY(symbol,date))')
    con.execute("CREATE TABLE IF NOT EXISTS universe_registry (ticker TEXT PRIMARY KEY, company TEXT, provider_symbol TEXT, status TEXT, confidence REAL, source TEXT, last_checked TEXT, last_success TEXT, attempts INTEGER DEFAULT 0, note TEXT)")
    con.commit(); con.close()
init_db()

def _merge_registry_universe(df):
    """Add successfully discovered new listings to the master universe."""
    try:
        con=dbcon()
        reg=pd.read_sql_query(
            "SELECT ticker,company FROM universe_registry WHERE status IN ('DIRECT','RESOLVED') AND provider_symbol IS NOT NULL AND provider_symbol!=''",
            con
        )
        con.close()
        if reg.empty:
            return df
        out=pd.concat([df[['ticker','company']],reg[['ticker','company']]],ignore_index=True)
        out['ticker']=out['ticker'].astype(str).str.upper().str.replace('.JK','',regex=False).str.strip()
        out['company']=out['company'].fillna(out['ticker']).astype(str)
        out=out[out.ticker.str.match(r'^[A-Z0-9]{2,6}$')].drop_duplicates('ticker',keep='last').reset_index(drop=True)
        return out
    except Exception:
        return df

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
                if len(out)>=100:return _merge_registry_universe(out),'remote universe + registry'
        except Exception: pass
    return _merge_registry_universe(pd.read_csv(SEED)),'bundled seed universe + registry'

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
        d['change_pct']=d.close.pct_change()*100
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
    if pd.notna(ma20) and ma20 != 0:
        ma_gap=(close/ma20-1)*100
        trend='BULLISH' if ma_gap>0.5 else ('BEARISH' if ma_gap<-0.5 else 'SIDEWAYS')
    else:
        ma_gap=np.nan; trend='N/A'
    return {'close':close,'change_pct':ch,'ma20':ma20,'ma_gap_pct':ma_gap,'trend':trend,'date':str(r.date)}

def _fetch_chart_symbol(symbol, period_days, interval):
    end=int(time.time()); start=end-int(period_days*86400)
    last_error=None
    params={'period1':start,'period2':end,'interval':interval,'events':'history','includeAdjustedClose':'true'}
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        url=f'https://{host}/v8/finance/chart/{symbol}'
        try:
            r=requests.get(url,params=params,timeout=CFGD['request_timeout_seconds'],headers={'User-Agent':CFGD['user_agent']})
            if r.status_code==404:
                last_error=requests.HTTPError(f'404 Not Found: {symbol}')
                continue
            r.raise_for_status()
            result=(r.json().get('chart') or {}).get('result') or []
            if not result:
                last_error=ValueError(f'Yahoo returned no chart result for {symbol}')
                continue
            j=result[0]; ts=j.get('timestamp',[]); q=(j.get('indicators') or {}).get('quote') or []
            if not ts or not q:
                last_error=ValueError(f'Yahoo returned empty history for {symbol}')
                continue
            qq=q[0]
            d=pd.DataFrame({'ts':pd.to_datetime(ts,unit='s',utc=True),'open':qq.get('open'),'high':qq.get('high'),'low':qq.get('low'),'close':qq.get('close'),'volume':qq.get('volume')}).dropna(subset=['close'])
            if not d.empty:return d
            last_error=ValueError(f'Yahoo returned empty price rows for {symbol}')
        except Exception as e:
            last_error=e
    if last_error: raise last_error
    return pd.DataFrame()

def _norm_text(value):
    s=str(value or '').upper()
    s=re.sub(r'[^A-Z0-9]+',' ',s)
    return ' '.join(s.split())

def _company_similarity(query_company, candidate_name):
    a=_norm_text(query_company); b=_norm_text(candidate_name)
    if not a or not b: return 0.0
    if a==b: return 1.0
    from difflib import SequenceMatcher
    seq=SequenceMatcher(None,a,b).ratio()
    at=set(a.split()); bt=set(b.split())
    stop={'TBK','PERSERO','INDONESIA'}
    at={x for x in at if x not in stop}; bt={x for x in bt if x not in stop}
    overlap=(len(at & bt)/max(1,len(at))) if at else 0.0
    return max(seq,0.65*overlap+0.35*seq)

def registry_get(ticker):
    try:
        con=dbcon(); r=con.execute('SELECT ticker,company,provider_symbol,status,confidence,source,last_checked,last_success,attempts,note FROM universe_registry WHERE ticker=?',(str(ticker).upper(),)).fetchone(); con.close()
        if not r:return None
        keys=['ticker','company','provider_symbol','status','confidence','source','last_checked','last_success','attempts','note']
        return dict(zip(keys,r))
    except Exception:return None

def registry_upsert(ticker, company='', provider_symbol='', status='UNKNOWN', confidence=0.0, source='resolver', note=''):
    now=datetime.now(timezone.utc).isoformat(); old=registry_get(ticker); attempts=int(old.get('attempts',0)) + 1 if old else 1
    last_success=(now if status in ('DIRECT','RESOLVED') else (old.get('last_success','') if old else ''))
    con=dbcon(); con.execute('INSERT OR REPLACE INTO universe_registry(ticker,company,provider_symbol,status,confidence,source,last_checked,last_success,attempts,note) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (str(ticker).upper(),str(company or (old or {}).get('company','')),str(provider_symbol or (old or {}).get('provider_symbol','')),status,float(confidence or 0),source,now,last_success,attempts,str(note or ''))); con.commit(); con.close()

@cache_data(600)
def resolve_yahoo_symbol(ticker, company=''):
    """Resolve a failed IDX ticker using Yahoo search plus company-name similarity."""
    ticker=str(ticker).upper().replace('.JK',''); company=str(company or '')
    reg=registry_get(ticker)
    if reg and reg.get('provider_symbol') and reg.get('status') in ('RESOLVED','DIRECT'):
        return reg.get('provider_symbol')
    candidates=[]; queries=[ticker]
    if company and _norm_text(company) not in queries: queries.append(company)
    for qterm in queries:
        for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
            try:
                r=requests.get(f'https://{host}/v1/finance/search',params={'q':qterm,'quotesCount':20,'newsCount':0},timeout=CFGD.get('request_timeout_seconds',8),headers={'User-Agent':CFGD['user_agent']})
                r.raise_for_status(); candidates.extend((r.json() or {}).get('quotes') or [])
            except Exception: continue
    seen=set(); scored=[]
    for q in candidates:
        sym=str(q.get('symbol','')).upper()
        if not sym.endswith('.JK') or str(q.get('quoteType','EQUITY')).upper()!='EQUITY' or sym in seen: continue
        seen.add(sym); cand_name=q.get('longname') or q.get('shortname') or q.get('name') or ''
        score=_company_similarity(company,cand_name); exact=float(sym==f'{ticker}.JK'); scored.append((exact,score,sym,cand_name))
    if not scored:
        registry_upsert(ticker,company,'','PROVIDER_NOT_FOUND',0.0,'yahoo_search','no .JK equity candidate returned'); return None
    scored.sort(reverse=True); best=next((x for x in scored if x[0]>0),scored[0]); min_sim=float(CFGD.get('resolver_min_company_similarity',0.72))
    if best[0]>0 or best[1]>=min_sim:
        status='DIRECT' if best[2]==f'{ticker}.JK' else 'RESOLVED'
        registry_upsert(ticker,company,best[2],status,max(best[0],best[1]),'yahoo_search',f'candidate={best[3]}'); return best[2]
    registry_upsert(ticker,company,'','PROVIDER_NOT_FOUND',best[1],'yahoo_search',f'best_candidate={best[2]} name={best[3]}'); return None

@cache_data(120)
def fetch_chart(ticker, period_days, interval):
    symbol=str(ticker).upper().replace('.JK','') + '.JK'
    return _fetch_chart_symbol(symbol, period_days, interval)

def fetch_chart_recover(ticker, period_days, interval, company=''):
    """Registry-aware recovery: mapped provider symbol -> direct -> company-aware search."""
    ticker=str(ticker).upper().replace('.JK',''); base=ticker+'.JK'; company=str(company or '')
    reg=registry_get(ticker); mapped=str(reg.get('provider_symbol') or '') if reg else ''
    if mapped and mapped!=base:
        try:
            d=_fetch_chart_symbol(mapped,period_days,interval); registry_upsert(ticker,company,mapped,'RESOLVED',float(reg.get('confidence') or 0.8),'registry','mapped provider symbol succeeded'); return d,'RESOLVED',mapped,''
        except Exception: pass
    try:
        d=_fetch_chart_symbol(base,period_days,interval); registry_upsert(ticker,company,base,'DIRECT',1.0,'direct','direct provider symbol succeeded'); return d,'DIRECT',base,''
    except Exception as direct_error:
        resolved=resolve_yahoo_symbol(ticker,company)
        if resolved and resolved!=base:
            try:
                d=_fetch_chart_symbol(resolved,period_days,interval); return d,'RESOLVED',resolved,''
            except Exception as resolved_error:
                registry_upsert(ticker,company,resolved,'UNRESOLVED',float((registry_get(ticker) or {}).get('confidence') or 0),'yahoo_search',str(resolved_error)[:180]); return pd.DataFrame(),'UNRESOLVED',resolved,str(resolved_error)[:180]
        registry_upsert(ticker,company,'','PROVIDER_NOT_FOUND' if '404' in str(direct_error) else 'UNRESOLVED',0.0,'resolver',str(direct_error)[:180]); return pd.DataFrame(),'UNRESOLVED',None,str(direct_error)[:180]

def cached_price_count(ticker):
    con=dbcon()
    try:
        row=con.execute('SELECT COUNT(*), MAX(date) FROM prices WHERE ticker=?',(str(ticker),)).fetchone()
        return int(row[0] or 0), (str(row[1]) if row[1] else '')
    finally:
        con.close()

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
    x['ret1']=c.pct_change(); x['ret5']=c.pct_change(5); x['ma20']=c.rolling(20).mean(); x['ma50']=c.rolling(50).mean()
    x['ma20_gap']=c/x.ma20-1; x['ma50_gap']=c/x.ma50-1
    delta=c.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean(); rs=gain/loss.replace(0,np.nan)
    x['rsi14']=100-(100/(1+rs)); ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean(); x['macd']=ema12-ema26; x['macd_signal']=x.macd.ewm(span=9,adjust=False).mean(); x['macd_gap']=(x.macd-x.macd_signal)/c
    tr=pd.concat([(x.high-x.low),(x.high-c.shift()).abs(),(x.low-c.shift()).abs()],axis=1).max(axis=1); x['atr14']=tr.rolling(14).mean(); x['atr_pct']=x.atr14/c
    x['vol20']=v.rolling(20).mean(); x['vol_ratio']=v/x.vol20.replace(0,np.nan); x['range_pct']=(x.high-x.low)/c
    x['ret3']=c.pct_change(3); x['ret10']=c.pct_change(10)
    x['rsi_slope']=x['rsi14'].diff(3)/3
    x['macd_slope']=x['macd'].diff(3)/3
    x['atr_pct_change']=x['atr_pct'].pct_change(5)
    x['vol_ratio_change']=x['vol_ratio'].pct_change(5)
    x['high20_gap']=c/c.rolling(20).max()-1
    x['low20_gap']=c/c.rolling(20).min()-1
    x['trend_strength']=(x['ma20_gap']-x['ma50_gap']).abs()
    return x

def load_ihsg_daily(con):
    # Always return a stable schema. On a fresh DB or when IHSG has not been
    # collected yet, pandas otherwise returns an empty frame without the
    # derived columns and downstream training can raise KeyError.
    cols=['date','close','ihsg_ret1','ihsg_ret5','ihsg_ret10','ihsg_ma20_gap','ihsg_vol20']
    try:
        d=pd.read_sql_query("SELECT date,close,change_pct FROM market_index WHERE symbol='IHSG' ORDER BY date",con)
    except Exception:
        return pd.DataFrame(columns=cols)
    if d.empty:
        return pd.DataFrame(columns=cols)
    d['date']=d['date'].astype(str)
    d['close']=pd.to_numeric(d['close'],errors='coerce')
    d=d.dropna(subset=['date','close']).copy()
    if d.empty:
        return pd.DataFrame(columns=cols)
    d['ihsg_ret1']=d['close'].pct_change()
    d['ihsg_ret5']=d['close'].pct_change(5)
    d['ihsg_ret10']=d['close'].pct_change(10)
    d['ihsg_ma20']=d['close'].rolling(20).mean()
    d['ihsg_ma20_gap']=d['close']/d['ihsg_ma20']-1
    d['ihsg_vol20']=d['close'].pct_change().rolling(20).std()
    return d.reindex(columns=cols)

def build_training_rows(con,tickers,horizon=None):
    """Build leakage-safe chronological rows with engineered features and configurable target horizon."""
    rows=[]; horizon=int(horizon or CFGD.get('primary_target_horizon',3)); th=float(CFGD.get('future_threshold',0.005))
    ih=load_ihsg_daily(con)
    ih_features=ih.reindex(columns=['date','ihsg_ret1','ihsg_ret5','ihsg_ret10','ihsg_ma20_gap','ihsg_vol20'])
    for t in tickers:
        df=pd.read_sql_query('SELECT date,open,high,low,close,volume FROM prices WHERE ticker=? ORDER BY date',con,params=(t,))
        if len(df)<100: continue
        z=indicators(df); z['date']=z['date'].astype(str); z=z.merge(ih_features,on='date',how='left')
        z['rel_ret1']=z.ret1-z.ihsg_ret1; z['rel_ret5']=z.ret5-z.ihsg_ret5; z['rel_ret10']=z.ret10-z.ihsg_ret10
        z['future_ret']=z.close.shift(-horizon)/z.close-1
        z=z.dropna(subset=FEATURES+['future_ret'])
        for _,r in z.iterrows():
            f=r[FEATURES].astype(float).values
            if not np.all(np.isfinite(f)): continue
            yv=float(r.future_ret); y=2 if yv>th else (0 if yv<-th else 1)
            rows.append((t,str(r.date),*f,int(y),yv))
    cols=['ticker','date']+FEATURES+['y','future_ret']; return pd.DataFrame(rows,columns=cols)

class NumpyJST:
    def __init__(self,d,h=16,c=3,seed=42):
        rng=np.random.default_rng(seed); self.W1=rng.normal(0,np.sqrt(2/d),(d,h)); self.b1=np.zeros(h); self.W2=rng.normal(0,np.sqrt(2/h),(h,c)); self.b2=np.zeros(c); self.mu=np.zeros(d); self.sd=np.ones(d)
    def softmax(self,z):
        z=z-z.max(axis=1,keepdims=True); e=np.exp(z); return e/e.sum(axis=1,keepdims=True)
    def fit(self,X,y,epochs=8,lr=.01,batch=512,class_weights=None):
        self.mu=X.mean(0); self.sd=X.std(0); self.sd[self.sd<1e-8]=1; X=(X-self.mu)/self.sd; n=len(X); rng=np.random.default_rng(7)
        cw=np.asarray(class_weights if class_weights is not None else [1.0,1.0,1.0],dtype=float)
        for _ in range(epochs):
            idx=rng.permutation(n)
            for s in range(0,n,batch):
                ii=idx[s:s+batch]; xb=X[ii]; yb=y[ii]; h=np.tanh(xb@self.W1+self.b1); p=self.softmax(h@self.W2+self.b2); one=np.zeros_like(p); one[np.arange(len(yb)),yb]=1
                sample_w=cw[yb].reshape(-1,1); dz2=(p-one)*sample_w/np.maximum(1.0,sample_w.sum()); dW2=h.T@dz2; db2=dz2.sum(0); dh=(dz2@self.W2.T)*(1-h*h); dW1=xb.T@dh; db1=dh.sum(0)
                self.W2-=lr*dW2; self.b2-=lr*db2; self.W1-=lr*dW1; self.b1-=lr*db1
    def predict_proba(self,X):
        X=(np.asarray(X)-self.mu)/self.sd; return self.softmax(np.tanh(X@self.W1+self.b1)@self.W2+self.b2)
    def accuracy(self,X,y): return float((self.predict_proba(X).argmax(1)==y).mean())


def classification_metrics(y_true, y_pred, proba=None):
    y_true=np.asarray(y_true,dtype=int); y_pred=np.asarray(y_pred,dtype=int)
    acc=float((y_true==y_pred).mean()) if len(y_true) else np.nan
    recalls=[]; precisions=[]; f1s=[]
    for c in range(3):
        tp=int(((y_true==c)&(y_pred==c)).sum()); fp=int(((y_true!=c)&(y_pred==c)).sum()); fn=int(((y_true==c)&(y_pred!=c)).sum())
        prec=tp/(tp+fp) if tp+fp else np.nan; rec=tp/(tp+fn) if tp+fn else np.nan
        f1=2*prec*rec/(prec+rec) if np.isfinite(prec) and np.isfinite(rec) and prec+rec else np.nan
        precisions.append(prec); recalls.append(rec); f1s.append(f1)
    bal=float(np.nanmean(recalls)) if recalls else np.nan
    macro_f1=float(np.nanmean(f1s)) if f1s else np.nan
    up_precision=float(precisions[2]); up_recall=float(recalls[2]); up_f1=float(f1s[2])
    return {'accuracy':acc,'balanced_accuracy':bal,'macro_f1':macro_f1,'down_f1':float(f1s[0]),'sideways_f1':float(f1s[1]),'up_precision':up_precision,'up_recall':up_recall,'up_f1':up_f1}

class NumpyLogistic:
    """Small dependency-free multinomial logistic baseline."""
    def __init__(self,d,c=3,seed=11):
        rng=np.random.default_rng(seed); self.W=rng.normal(0,0.01,(d,c)); self.b=np.zeros(c); self.mu=np.zeros(d); self.sd=np.ones(d)
    def softmax(self,z):
        z=z-z.max(axis=1,keepdims=True); e=np.exp(z); return e/e.sum(axis=1,keepdims=True)
    def fit(self,X,y,epochs=35,lr=.03,batch=1024,class_weights=None):
        X=np.asarray(X,float); y=np.asarray(y,int); self.mu=X.mean(0); self.sd=X.std(0); self.sd[self.sd<1e-8]=1; X=(X-self.mu)/self.sd
        cw=np.asarray(class_weights if class_weights is not None else [1.0,1.0,1.0],dtype=float)
        rng=np.random.default_rng(17); n=len(X)
        for _ in range(epochs):
            idx=rng.permutation(n)
            for st in range(0,n,batch):
                ii=idx[st:st+batch]; xb=X[ii]; yb=y[ii]; p=self.softmax(xb@self.W+self.b); one=np.zeros_like(p); one[np.arange(len(yb)),yb]=1
                sample_w=cw[yb].reshape(-1,1); dz=(p-one)*sample_w/np.maximum(1.0,sample_w.sum()); self.W-=lr*(xb.T@dz); self.b-=lr*dz.sum(0)
        return self
    def predict_proba(self,X):
        X=(np.asarray(X,float)-self.mu)/self.sd; return self.softmax(X@self.W+self.b)


def inverse_frequency_weights(y, n_classes=3):
    y=np.asarray(y,dtype=int)
    counts=np.bincount(y,minlength=n_classes).astype(float)
    total=max(1.0,counts.sum())
    w=total/(n_classes*np.maximum(counts,1.0))
    w=w/np.mean(w)
    return w

def confusion_counts(y_true,y_pred,n_classes=3):
    cm=np.zeros((n_classes,n_classes),dtype=int)
    for a,b in zip(np.asarray(y_true,dtype=int),np.asarray(y_pred,dtype=int)):
        if 0<=a<n_classes and 0<=b<n_classes: cm[a,b]+=1
    return cm

def majority_predict(train_y,n):
    vals,cnt=np.unique(np.asarray(train_y,dtype=int),return_counts=True); c=int(vals[cnt.argmax()]) if len(vals) else 1
    p=np.zeros((n,3)); p[:,c]=1.0; return p


def validation_report(universe,folds=4):
    """Leakage-safe chronological comparison across multiple target horizons."""
    con=dbcon(); all_details=[]; summaries=[]
    horizons=[int(x) for x in CFGD.get('validation_target_horizons',[1,3,5])]
    for horizon in horizons:
        tr=build_training_rows(con,universe.ticker.tolist(),horizon=horizon)
        if len(tr)<1000: continue
        tr=tr.sort_values('date').reset_index(drop=True); max_rows=int(CFGD.get('validation_max_rows',150000))
        if len(tr)>max_rows: tr=tr.iloc[-max_rows:].reset_index(drop=True)
        n=len(tr)
        for i in range(1,folds+1):
            train_end=int(n*(0.40+0.10*(i-1))); test_end=int(n*(0.52+0.10*(i-1)))
            if test_end<=train_end or train_end<400: continue
            train=tr.iloc[:train_end]; test=tr.iloc[train_end:test_end]
            Xtr=train[FEATURES].values.astype(float); ytr=train.y.values.astype(int); Xte=test[FEATURES].values.astype(float); yte=test.y.values.astype(int)
            models={}
            cw=inverse_frequency_weights(ytr)
            jm=NumpyJST(len(FEATURES),CFGD['jst_hidden'],seed=100+i+horizon); jm.fit(Xtr,ytr,max(4,int(CFGD['jst_epochs']//2)),CFGD['jst_learning_rate'],CFGD['jst_batch_size']); models['JST']=jm.predict_proba(Xte)
            jmw=NumpyJST(len(FEATURES),CFGD['jst_hidden'],seed=500+i+horizon); jmw.fit(Xtr,ytr,max(4,int(CFGD['jst_epochs']//2)),CFGD['jst_learning_rate'],CFGD['jst_batch_size'],class_weights=cw); models['JST_WEIGHTED']=jmw.predict_proba(Xte)
            lm=NumpyLogistic(len(FEATURES),3,seed=200+i+horizon); lm.fit(Xtr,ytr,epochs=35,lr=.03,batch=1024); models['LOGISTIC']=lm.predict_proba(Xte)
            lmw=NumpyLogistic(len(FEATURES),3,seed=600+i+horizon); lmw.fit(Xtr,ytr,epochs=35,lr=.03,batch=1024,class_weights=cw); models['LOGISTIC_WEIGHTED']=lmw.predict_proba(Xte)
            models['MAJORITY']=majority_predict(ytr,len(Xte))
            for name,pr in models.items():
                yp=pr.argmax(1); m=classification_metrics(yte,yp,pr); cm=confusion_counts(yte,yp); all_details.append({'horizon_days':horizon,'fold':i,'model':name,'train_rows':len(train),'test_rows':len(test),'test_start':str(test.date.min()),'test_end':str(test.date.max()),'cm_00':int(cm[0,0]),'cm_01':int(cm[0,1]),'cm_02':int(cm[0,2]),'cm_10':int(cm[1,0]),'cm_11':int(cm[1,1]),'cm_12':int(cm[1,2]),'cm_20':int(cm[2,0]),'cm_21':int(cm[2,1]),'cm_22':int(cm[2,2]),**m})
    detail=pd.DataFrame(all_details)
    if detail.empty:return detail,pd.DataFrame()
    summary=detail.groupby(['horizon_days','model'],as_index=False).agg(folds=('fold','count'),accuracy_mean=('accuracy','mean'),accuracy_std=('accuracy','std'),balanced_accuracy_mean=('balanced_accuracy','mean'),macro_f1_mean=('macro_f1','mean'),up_precision_mean=('up_precision','mean'),up_recall_mean=('up_recall','mean'),up_f1_mean=('up_f1','mean'))
    summary['accuracy_std']=summary['accuracy_std'].fillna(0.0)
    # Keep model selection transparent: add macro-F1 derived from the three class F1s
    # when available in fold detail. Older runs remain readable without it.
    if {'down_f1','sideways_f1','up_f1'}.issubset(detail.columns):
        mf=detail.groupby(['horizon_days','model'],as_index=False).agg(macro_f1_mean=('macro_f1','mean')) if 'macro_f1' in detail.columns else pd.DataFrame()
        if not mf.empty: summary=summary.merge(mf,on=['horizon_days','model'],how='left')
    summary=summary.sort_values(['horizon_days','balanced_accuracy_mean','up_precision_mean'],ascending=[True,False,False]).reset_index(drop=True)
    # Diagnose target balance and aggregate confusion matrices from the same
    # chronological folds. This is deliberately based on train-fold/test-fold
    # data, never on future observations.
    distributions=[]
    confusion=[]
    dist_con=dbcon()
    for horizon in sorted(detail.horizon_days.unique()):
        src=build_training_rows(dist_con,universe.ticker.tolist(),horizon=int(horizon))
        counts=src['y'].value_counts().reindex([0,1,2],fill_value=0)
        total=int(counts.sum())
        distributions.append({'horizon_days':int(horizon),'down':int(counts[0]),'sideways':int(counts[1]),'up':int(counts[2]),'total':total,'down_pct':float(counts[0]/max(1,total)),'sideways_pct':float(counts[1]/max(1,total)),'up_pct':float(counts[2]/max(1,total))})
        for model in detail.model.unique():
            sub=detail[(detail.horizon_days==horizon)&(detail.model==model)]
            keys=['cm_00','cm_01','cm_02','cm_10','cm_11','cm_12','cm_20','cm_21','cm_22']
            vals={k:int(sub[k].sum()) for k in keys}
            confusion.append({'horizon_days':int(horizon),'model':model,**vals})
    payload={'generated_at':datetime.now(timezone.utc).isoformat(),'primary_horizon':int(CFGD.get('primary_target_horizon',3)),'summary':summary.to_dict(orient='records'),'folds':detail.to_dict(orient='records'),'target_distribution':distributions,'confusion_matrices':confusion}
    dist_con.close()
    con.close()
    with open(os.path.join(DB_DIR,'model_validation.json'),'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    primary=int(CFGD.get('primary_target_horizon',3)); ps=summary[summary.horizon_days==primary]
    if not ps.empty:
        best=ps.iloc[0]; state_set('validation_best_model',best['model']); state_set('validation_best_balanced_accuracy',float(best['balanced_accuracy_mean'])); state_set('validation_at',datetime.now().isoformat())
        for _,r in ps.iterrows():
            key='validation_'+str(r['model']).lower(); state_set(key+'_accuracy',float(r['accuracy_mean'])); state_set(key+'_balanced',float(r['balanced_accuracy_mean'])); state_set(key+'_up_precision',float(r['up_precision_mean']))
    return detail,summary


def save_active_model(model, model_name, horizon, train_rows):
    path=os.path.join(DB_DIR,'active_model_v1883.npz')
    payload={'model_name':str(model_name),'horizon_days':int(horizon),'train_rows':int(train_rows),'trained_at':datetime.now(timezone.utc).isoformat()}
    if model_name.startswith('LOGISTIC'):
        np.savez(path,W=model.W,b=model.b,mu=model.mu,sd=model.sd)
    elif model_name.startswith('JST'):
        np.savez(path,W1=model.W1,b1=model.b1,W2=model.W2,b2=model.b2,mu=model.mu,sd=model.sd)
    else:
        return
    with open(os.path.join(DB_DIR,'active_model_meta.json'),'w',encoding='utf-8') as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
    state_set('active_model_name',model_name); state_set('active_model_horizon_days',horizon); state_set('active_model_trained_at',payload['trained_at']); state_set('active_model_train_rows',train_rows)

def load_active_model():
    meta_path=os.path.join(DB_DIR,'active_model_meta.json'); path=os.path.join(DB_DIR,'active_model_v1883.npz')
    try:
        with open(meta_path,'r',encoding='utf-8') as f: meta=json.load(f)
        name=str(meta.get('model_name',''))
        if not os.path.exists(path): return load_jst()
        z=np.load(path)
        if name=='LOGISTIC':
            m=NumpyLogistic(len(FEATURES),3); m.W=z['W']; m.b=z['b']; m.mu=z['mu']; m.sd=z['sd']; return m
        if name=='LOGISTIC_WEIGHTED':
            m=NumpyLogistic(len(FEATURES),3); m.W=z['W']; m.b=z['b']; m.mu=z['mu']; m.sd=z['sd']; return m
        if name=='JST':
            m=NumpyJST(len(FEATURES),CFGD['jst_hidden']); m.W1=z['W1']; m.b1=z['b1']; m.W2=z['W2']; m.b2=z['b2']; m.mu=z['mu']; m.sd=z['sd']; return m
        if name=='JST_WEIGHTED':
            m=NumpyJST(len(FEATURES),CFGD['jst_hidden']); m.W1=z['W1']; m.b1=z['b1']; m.W2=z['W2']; m.b2=z['b2']; m.mu=z['mu']; m.sd=z['sd']; return m
    except Exception:
        return load_jst()
    return load_jst()

def train_deployed_model(universe):
    """Promote the best validated model for the primary horizon, then refit it on all available historical rows."""
    meta_path=os.path.join(DB_DIR,'model_validation.json')
    try:
        with open(meta_path,'r',encoding='utf-8') as f: payload=json.load(f)
        summary=pd.DataFrame(payload.get('summary',[])); primary=int(payload.get('primary_horizon',CFGD.get('primary_target_horizon',3)))
    except Exception:
        return None, 'NO_VALIDATION'
    if summary.empty: return None,'NO_VALIDATION'
    ps=summary[summary.horizon_days==primary].copy()
    if ps.empty: return None,'NO_PRIMARY'
    # Select by balanced accuracy first, then macro-F1, then UP precision. This avoids
    # promoting a model merely because raw accuracy benefits from class imbalance.
    if 'macro_f1_mean' not in ps.columns: ps['macro_f1_mean']=0.0
    ps=ps[~ps.model.eq('MAJORITY')].copy()
    if ps.empty: return None,'NO_MODEL'
    ps=ps.sort_values(['balanced_accuracy_mean','macro_f1_mean','up_precision_mean'],ascending=[False,False,False]).reset_index(drop=True)
    name=str(ps.iloc[0].model)
    con=dbcon(); tr=build_training_rows(con,universe.ticker.tolist(),horizon=primary); con.close()
    if len(tr)<500: return None,'INSUFFICIENT_DATA'
    tr=tr.sort_values('date').reset_index(drop=True)
    X=tr[FEATURES].values.astype(float); y=tr.y.values.astype(int); cw=inverse_frequency_weights(y)
    if name=='LOGISTIC_WEIGHTED':
        model=NumpyLogistic(len(FEATURES),3,seed=600+primary); model.fit(X,y,epochs=45,lr=.03,batch=1024,class_weights=cw)
    elif name=='LOGISTIC':
        model=NumpyLogistic(len(FEATURES),3,seed=200+primary); model.fit(X,y,epochs=45,lr=.03,batch=1024)
    elif name=='JST_WEIGHTED':
        model=NumpyJST(len(FEATURES),CFGD['jst_hidden'],seed=500+primary); model.fit(X,y,max(8,int(CFGD['jst_epochs'])),CFGD['jst_learning_rate'],CFGD['jst_batch_size'],class_weights=cw)
    else:
        model=NumpyJST(len(FEATURES),CFGD['jst_hidden'],seed=100+primary); model.fit(X,y,max(8,int(CFGD['jst_epochs'])),CFGD['jst_learning_rate'],CFGD['jst_batch_size'])
    save_active_model(model,name,primary,len(tr))
    state_set('active_model_validated_balanced',float(ps.iloc[0]['balanced_accuracy_mean']))
    state_set('active_model_validated_macro_f1',float(ps.iloc[0].get('macro_f1_mean',0.0)))
    return model,name

def train_jst(universe):
    horizon=int(CFGD.get('primary_target_horizon',3)); con=dbcon(); tr=build_training_rows(con,universe.ticker.tolist(),horizon=horizon); con.close()
    if len(tr)<500:return None,0,None
    tr=tr.sort_values('date'); cut=max(1,int(len(tr)*0.8)); train=tr.iloc[:cut]; test=tr.iloc[cut:]
    X=train[FEATURES].values.astype(float); y=train.y.values.astype(int); Xt=test[FEATURES].values.astype(float); yt=test.y.values.astype(int)
    model=NumpyJST(len(FEATURES),CFGD['jst_hidden']); model.fit(X,y,CFGD['jst_epochs'],CFGD['jst_learning_rate'],CFGD['jst_batch_size']); acc=model.accuracy(Xt,yt) if len(test) else None
    np.savez(os.path.join(DB_DIR,'jst_model_v188.npz'),W1=model.W1,b1=model.b1,W2=model.W2,b2=model.b2,mu=model.mu,sd=model.sd)
    state_set('jst_target_horizon_days',horizon); state_set('jst_trained_at',datetime.now().isoformat()); state_set('jst_test_accuracy',acc if acc is not None else ''); state_set('jst_train_rows',len(tr)); return model,len(tr),acc

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
        m=classification_metrics(yt,yp,pred); acc=m['accuracy']; up_precision=m['up_precision']; results.append({'fold':i,'train_rows':len(train),'test_rows':len(test),'test_accuracy':acc,'balanced_accuracy':m['balanced_accuracy'],'up_precision':up_precision,'up_recall':m['up_recall'],'up_f1':m['up_f1'],'test_start':str(test.date.min()),'test_end':str(test.date.max())})
    out=pd.DataFrame(results); mean=float(out.test_accuracy.mean()) if not out.empty else None
    state_set('walk_forward_accuracy',mean if mean is not None else ''); state_set('walk_forward_folds',len(out)); state_set('walk_forward_at',datetime.now().isoformat()); return out,mean

def load_jst():
    p=os.path.join(DB_DIR,'jst_model_v188.npz')
    if not os.path.exists(p):return None
    try:
        z=np.load(p); m=NumpyJST(len(FEATURES),CFGD['jst_hidden']); m.W1=z['W1'];m.b1=z['b1'];m.W2=z['W2'];m.b2=z['b2'];m.mu=z['mu'];m.sd=z['sd']; return m
    except Exception:return None

def sector_proxy(company, ticker):
    """Best-effort sector proxy from company name/ticker. It is explicitly a proxy,
    not an official IDX classification, and unmapped names remain MIXED."""
    s=(str(company)+' '+str(ticker)).upper()
    rules=[
        ('FINANCIALS',['BANK','BANKING','FINANCE','MULTIFINANCE','PEMBIAYAAN','ASURANSI','INSURANCE','SECURITIES','SEKURITAS','INVESTAMA','INVESTASI']),
        ('ENERGY',['COAL','ENERGI','ENERGY','MINING','MINERAL','TAMBANG','PETRO','OIL','GAS','BATUBARA']),
        ('BASIC_MATERIALS',['CEMENT','SEMEN','STEEL','BAJA','CHEMICAL','KIMIA','PULP','PAPER','KERTAS','GLASS','METAL','LOGAM']),
        ('CONSUMER',['FOOD','FOODS','BEVERAGE','CONSUMER','RETAIL','MART','PHARM','FARMASI','TOBACCO','ROKOK','PRODUKSI RUMAH TANGGA']),
        ('INDUSTRIALS',['INDUSTRI','INDUSTRIAL','ENGINEERING','MANUFACTUR','MANUFAKTUR','MACHIN','HEAVY EQUIPMENT','KONSTRUKSI','CONSTRUCTION','OTOMOTIF','AUTOMOTIVE']),
        ('PROPERTY',['PROPERTY','PROPERTI','REALTY','REAL ESTATE','DEVELOPMENT','DEVELOPER']),
        ('INFRASTRUCTURE',['TELEKOM','TELECOMM','TOWER','INFRA','INFRASTRUKTUR','TOL','TOLL','UTILITIES']),
        ('TRANSPORTATION',['TRANSPORT','LOGISTICS','LOGISTIK','SHIPPING','PELAYARAN','AIRPORT','BANDARA','PORT','PELABUHAN']),
        ('TECHNOLOGY',['TECH','TECHNOLOGY','DIGITAL','SOFTWARE','DATA','MEDIA','INTERNET','CLOUD']),
        ('HEALTHCARE',['HEALTH','HOSPITAL','RUMAH SAKIT','MEDIKA','PHARMA','PHARMACY']),
    ]
    for name,keys in rules:
        if any(k in s for k in keys): return name
    return 'MIXED'

def sector_intelligence(universe, market_df=None):
    """Calculate relative 5D strength of proxy sectors from stored prices."""
    rows=[]
    for _,u in universe.iterrows():
        sec=sector_proxy(u.company,u.ticker)
        try:
            # handled by caller with price data; this function only labels
            rows.append((u.ticker,sec))
        except Exception: rows.append((u.ticker,sec))
    return pd.DataFrame(rows,columns=['ticker','sector'])


def market_regime(ihsg_df):
    if ihsg_df is None or ihsg_df.empty: return 'UNKNOWN',50.0
    d=ihsg_df.copy()
    if 'close' not in d.columns:
        return 'UNKNOWN', 50.0
    c=pd.to_numeric(d['close'], errors='coerce').dropna()
    if c.empty: return 'UNKNOWN', 50.0
    r5=float(c.pct_change(5).iloc[-1]) if len(c)>5 else 0.0
    ma20=float(c.tail(20).mean()) if len(c)>=20 else float(c.iloc[-1])
    gap=float(c.iloc[-1]/ma20-1) if ma20 else 0.0
    vol=float(c.pct_change().tail(20).std()) if len(c)>=20 else 0.0
    score=float(np.clip(50+gap*260+r5*120,0,100))
    if vol>0.018: return 'HIGH_VOLATILITY',score
    if score>=62: return 'BULLISH',score
    if score<=38: return 'BEARISH',score
    return 'SIDEWAYS',score


def classify_decision(row):
    p_up=float(row.jst_p_up) if pd.notna(row.jst_p_up) else np.nan
    p_down=float(row.jst_p_down) if pd.notna(row.jst_p_down) else np.nan
    conf=float(row.jst_confidence) if pd.notna(row.jst_confidence) else 0.0
    score=float(row.ai_score); risk=str(row.risk); regime=str(row.market_regime); trend=str(row.trend)
    rank_pct=float(row.relative_percentile) if pd.notna(row.relative_percentile) else 0.50
    rel=float(row.relative_strength) if pd.notna(row.relative_strength) else 50.0
    sector=float(row.sector_score) if pd.notna(row.sector_score) else 50.0
    rr=float(row.risk_reward_2) if pd.notna(row.risk_reward_2) else 0.0
    # Decision thresholds are relative to the current universe, then gated by
    # probability, confidence, trend, risk and executable risk/reward.
    buy_rank=float(CFGD.get('relative_buy_percentile',0.985))
    near_rank=float(CFGD.get('relative_near_percentile',0.95))
    watch_rank=float(CFGD.get('relative_watch_percentile',0.88))
    sell_rank=float(CFGD.get('relative_sell_percentile',0.025))
    buy_prob=float(CFGD.get('relative_buy_probability_min',0.56))
    near_prob=float(CFGD.get('relative_near_probability_min',0.50))
    conf_gate=float(CFGD.get('relative_confidence_min',0.36))
    min_rr=float(CFGD.get('min_risk_reward_2',1.50))
    block_high=bool(CFGD.get('block_high_risk_signals',True)) and risk=='HIGH'
    strong_relative = rel>=52 and sector>=48
    bearish_penalty = 0.04 if regime=='BEARISH' else (0.05 if regime=='HIGH_VOLATILITY' else (0.015 if regime=='SIDEWAYS' else 0.0))
    regime_rank_gate = buy_rank if regime=='BULLISH' else max(buy_rank, 0.99 if regime in ('BEARISH','HIGH_VOLATILITY') else 0.985)
    regime_conf_gate = conf_gate + (0.05 if regime in ('BEARISH','HIGH_VOLATILITY') else (0.02 if regime=='SIDEWAYS' else 0.0))
    regime_rel_gate = 58 if regime in ('BEARISH','HIGH_VOLATILITY') else (54 if regime=='SIDEWAYS' else 52)
    if (rank_pct>=regime_rank_gate and p_up==p_up and p_up>=buy_prob+bearish_penalty and
        conf>=regime_conf_gate and trend=='BULLISH' and strong_relative and rel>=regime_rel_gate and rr>=min_rr and not block_high):
        ds=float(np.clip(.45*score+30*p_up*100+15*conf*100+10*min(rr/3,1)*100,0,100))
        return 'BUY',ds,'BUY: relative rank + model probability + trend + sector/relative strength + risk/reward agree'
    if (p_down==p_down and p_down>=float(CFGD.get('relative_sell_probability_min',0.56)) and
        rank_pct<=sell_rank and conf>=conf_gate and not block_high):
        ds=float(np.clip(.45*(100-score)+35*p_down*100+20*conf*100,0,100))
        return 'SELL/AVOID',ds,'SELL/AVOID: downside probability + weak relative rank + confidence agree'
    if (rank_pct>=near_rank and p_up==p_up and p_up>=near_prob and conf>=conf_gate and
        rel>=50 and sector>=45 and rr>=min_rr and not block_high):
        ds=float(np.clip(.50*score+30*p_up*100+15*conf*100+5*min(rr/3,1)*100,0,100))
        return 'NEAR ENTRY',ds,'NEAR ENTRY: strong relative rank, confirmation and risk/reward; waiting for cleaner entry'
    if (rank_pct>=watch_rank and score>=50 and conf>=0.30 and (risk!='HIGH' or score>=62)):
        ds=float(np.clip(.60*score+25*(p_up*100 if np.isfinite(p_up) else 50)+15*conf*100,0,100))
        return 'WATCH',ds,'WATCH: upper-universe candidate, but confirmation or risk/reward is incomplete'
    return 'WAIT',float(np.clip(score,0,100)),'WAIT: relative rank, confirmation or risk/reward is insufficient'

def analyze_all(universe,model=None):
    con=dbcon(); parts=[]
    ih=load_ihsg_daily(con); ih_latest=ih.iloc[-1].to_dict() if not ih.empty else {}
    ihsg_gap=float(ih_latest.get('ihsg_ma20_gap',0) or 0); ihsg_r5=float(ih_latest.get('ihsg_ret5',0) or 0); ihsg_r1=float(ih_latest.get('ihsg_ret1',0) or 0)
    regime,market_score=market_regime(ih)
    # Build proxy sector labels and momentum from stored prices.
    secmap={str(u.ticker):sector_proxy(u.company,u.ticker) for _,u in universe.iterrows()}
    sector_returns={}
    for t,sec in secmap.items():
        df0=pd.read_sql_query('SELECT close FROM prices WHERE ticker=? ORDER BY date',con,params=(t,))
        if len(df0)>=6: sector_returns.setdefault(sec,[]).append(float(df0.close.iloc[-1]/df0.close.iloc[-6]-1))
    sector_score={k:float(np.clip(50+np.mean(v)*180,0,100)) for k,v in sector_returns.items() if v}
    sector_market_mean=float(np.mean(list(sector_score.values()))) if sector_score else 50.0
    for t in universe.ticker.tolist():
        df=pd.read_sql_query('SELECT * FROM prices WHERE ticker=? ORDER BY date',con,params=(t,))
        if len(df)<60: continue
        z=indicators(df); r=z.iloc[-1]; base=50.0
        base+=max(-10,min(10,(r.rsi14-50)*.25)) if pd.notna(r.rsi14) else 0
        base+=(12 if r.ma20>r.ma50 else -12) if pd.notna(r.ma20) and pd.notna(r.ma50) else 0
        base+=(10 if r.macd>r.macd_signal else -10) if pd.notna(r.macd) and pd.notna(r.macd_signal) else 0
        base+=max(-5,min(5,(r.vol_ratio-1)*5)) if pd.notna(r.vol_ratio) else 0
        base+=max(-8,min(8,r.ret5*100)) if pd.notna(r.ret5) else 0
        base=float(np.clip(base,0,100))
        feat=dict(r); ihsg_r10=float(ih_latest.get('ihsg_ret10',0) or 0); ihsg_vol=float(ih_latest.get('ihsg_vol20',0) or 0)
        feat.update({'ihsg_ret1':ihsg_r1,'ihsg_ret5':ihsg_r5,'ihsg_ret10':ihsg_r10,'ihsg_ma20_gap':ihsg_gap,'ihsg_vol20':ihsg_vol,'rel_ret1':float(r.ret1)-ihsg_r1 if pd.notna(r.ret1) else np.nan,'rel_ret5':float(r.ret5)-ihsg_r5 if pd.notna(r.ret5) else np.nan,'rel_ret10':float(r.ret10)-ihsg_r10 if pd.notna(r.ret10) else np.nan})
        f=np.array([[feat[k] for k in FEATURES]],dtype=float)
        jst_p=model.predict_proba(f)[0] if model is not None and np.all(np.isfinite(f)) else np.array([np.nan,np.nan,np.nan])
        if np.all(np.isfinite(jst_p)):
            p_down,p_side,p_up=jst_p; jst_signal=CLASS_NAMES[int(jst_p.argmax())]; jst_conf=float(jst_p.max())
        else: p_down=p_side=p_up=np.nan; jst_signal='N/A'; jst_conf=np.nan
        sector=secmap.get(t,'MIXED'); sscore=sector_score.get(sector,sector_market_mean)
        relative_strength=float(np.clip(50+(float(r.ret5)-ihsg_r5)*180,0,100)) if pd.notna(r.ret5) else 50.0
        score=.32*base + .28*(p_up*100 if np.isfinite(p_up) else base) + .15*market_score + .15*sscore + .10*relative_strength
        trend='BULLISH' if pd.notna(r.ma20) and pd.notna(r.ma50) and r.ma20>r.ma50 else 'BEARISH'
        # Market-aware filter: IHSG is not decoration. Defensive regimes reward
        # relative strength and bullish structure, while weak names lose weight.
        if regime in ('BEARISH','HIGH_VOLATILITY'):
            if relative_strength >= 58 and trend == 'BULLISH': score += 6.0
            elif relative_strength < 48 or trend != 'BULLISH': score -= 8.0
            else: score -= 2.0
        elif regime == 'SIDEWAYS':
            score += 3.0 if relative_strength >= 55 and trend == 'BULLISH' else (-3.0 if relative_strength < 48 else 0.0)
        elif regime == 'BULLISH':
            score += 3.0 if trend == 'BULLISH' else -2.0
        atr=float(r.atr14) if pd.notna(r.atr14) else np.nan; close=float(r.close)
        # Risk engine: combine volatility, momentum extremes and liquidity proxy.
        atr_pct=float(r.atr_pct) if pd.notna(r.atr_pct) else 0.0
        vol_ratio=float(r.vol_ratio) if pd.notna(r.vol_ratio) else 1.0
        risk=float(np.clip(35+(atr_pct*100*10)+(8 if r.rsi14>75 or r.rsi14<25 else 0)+(8 if vol_ratio>2.5 else 0),0,100))
        risk_label='LOW' if risk<40 else ('MEDIUM' if risk<65 else 'HIGH')
        if pd.notna(atr) and atr>0:
            recent_low=float(z['low'].tail(20).min()) if 'low' in z.columns else close-2*atr
            support_stop=max(close-2.0*atr, recent_low-0.25*atr)
            sl=float(min(close-1.0*atr, support_stop))
            risk_per_share=max(close-sl,0.5*atr)
            entry_low=max(sl+0.75*risk_per_share, close-0.50*atr)
            entry_high=close+0.10*atr
            tp1=close+1.50*risk_per_share; tp2=close+2.50*risk_per_share
            rr1=(tp1-close)/risk_per_share if risk_per_share>0 else np.nan
            rr2=(tp2-close)/risk_per_share if risk_per_share>0 else np.nan
            invalidation=f'Close < {sl:.4f} or bullish conditions materially weaken'
        else:
            sl=entry_low=entry_high=tp1=tp2=rr1=rr2=np.nan; risk_per_share=np.nan; invalidation='Insufficient ATR history'
        parts.append({'ticker':t,'company':universe.loc[universe.ticker==t,'company'].iloc[0],'date':r.date,'close':close,'change_1d':r.ret1,'rsi14':r.rsi14,'vol_ratio':r.vol_ratio,'trend':trend,'baseline_score':base,'jst_p_down':p_down,'jst_p_sideways':p_side,'jst_p_up':p_up,'jst_signal':jst_signal,'jst_confidence':jst_conf,'market_score':market_score,'market_regime':regime,'sector':sector,'sector_score':sscore,'relative_strength':relative_strength,'ai_score':float(np.clip(score,0,100)),'risk':risk_label,'risk_score':risk,'risk_per_share':risk_per_share,'entry_low':entry_low,'entry_high':entry_high,'stop_loss':sl,'target_1':tp1,'target_2':tp2,'risk_reward_1':rr1,'risk_reward_2':rr2,'invalidation':invalidation})
    con.close(); out=pd.DataFrame(parts)
    if out.empty:return out
    # Universe-relative ranking: blend model score, relative strength, sector strength and
    # directional probability. Percentile 1.0 means strongest in the current analyzed universe.
    p_up_series=pd.to_numeric(out['jst_p_up'],errors='coerce').fillna(0.50)
    out['relative_rank_score']=(
        0.55*out['ai_score'] +
        0.20*out['relative_strength'] +
        0.15*out['sector_score'] +
        0.10*(p_up_series*100.0)
    )
    out['relative_percentile']=out['relative_rank_score'].rank(pct=True,method='average')
    decisions=[classify_decision(rr) for _,rr in out.iterrows()]
    out['decision']=[x[0] for x in decisions]; out['decision_score']=[x[1] for x in decisions]; out['decision_reason']=[x[2] for x in decisions]
    out['decision_priority']=out.decision.map({'BUY':4,'NEAR ENTRY':3,'SELL/AVOID':2,'WATCH':1,'WAIT':0}).fillna(0)
    return out.sort_values(['decision_priority','decision_score','ai_score'],ascending=[False,False,False]).reset_index(drop=True)


def data_quality_audit(universe, analyzed):
    """Explain why universe symbols are absent from the analyzed decision set."""
    analyzed_set=set(analyzed.ticker.astype(str)) if analyzed is not None and not analyzed.empty else set()
    rows=[]; con=dbcon()
    try:
        for _,u in universe.iterrows():
            t=str(u.ticker)
            if t in analyzed_set:
                continue
            n=int(con.execute('SELECT COUNT(*) FROM prices WHERE ticker=?',(t,)).fetchone()[0])
            if n < 60: reason='INSUFFICIENT PRICE HISTORY'
            elif n == 0: reason='NO PRICE DATA'
            else: reason='FEATURES INVALID / INCOMPLETE'
            rows.append({'ticker':t,'company':str(u.company),'rows':n,'reason':reason})
    finally:
        con.close()
    return pd.DataFrame(rows)


def risk_engine_audit(res):
    """Summarize whether the risk engine produced executable levels for the analyzed universe."""
    if res is None or res.empty:
        return {}
    n=len(res)
    atr_ok=pd.to_numeric(res.get('risk_per_share'),errors='coerce').notna().sum() if 'risk_per_share' in res else 0
    entry_ok=(pd.to_numeric(res.get('entry_low'),errors='coerce').notna() & pd.to_numeric(res.get('entry_high'),errors='coerce').notna()).sum() if 'entry_low' in res and 'entry_high' in res else 0
    sl_ok=pd.to_numeric(res.get('stop_loss'),errors='coerce').notna().sum() if 'stop_loss' in res else 0
    rr_ok=pd.to_numeric(res.get('risk_reward_2'),errors='coerce').notna().sum() if 'risk_reward_2' in res else 0
    rr_good=(pd.to_numeric(res.get('risk_reward_2'),errors='coerce')>=float(CFGD.get('min_risk_reward_2',1.5))).sum() if 'risk_reward_2' in res else 0
    high=(res['risk'].eq('HIGH')).sum() if 'risk' in res else 0
    return {'analyzed':n,'risk_levels':int(atr_ok),'entry_levels':int(entry_ok),'stop_loss':int(sl_ok),'rr_available':int(rr_ok),'rr_qualified':int(rr_good),'high_risk':int(high),'coverage_pct':100*atr_ok/n if n else 0,'rr_qualified_pct':100*rr_good/n if n else 0}


def intelligence_detail(res):
    if res is None or res.empty: return pd.DataFrame()
    d=res.copy()
    d['rank_pct']=d['relative_percentile']*100
    d['p_up_pct']=pd.to_numeric(d['jst_p_up'],errors='coerce')*100
    d['p_down_pct']=pd.to_numeric(d['jst_p_down'],errors='coerce')*100
    d['confidence_pct']=pd.to_numeric(d['jst_confidence'],errors='coerce')*100
    d['distance_to_top']=(1-d['relative_percentile'])*100
    return d[['rank','ticker','company','decision','decision_reason','relative_rank_score','rank_pct','ai_score','p_up_pct','p_down_pct','confidence_pct','relative_strength','sector','sector_score','market_regime','trend','risk','entry_low','entry_high','stop_loss','target_1','target_2','risk_reward_1','risk_reward_2','invalidation']].copy()

def _intraday_load(ticker):
    con=dbcon(); df=pd.read_sql_query('SELECT ts,open,high,low,close,volume FROM intraday WHERE ticker=? ORDER BY ts',con,params=(ticker,)); con.close()
    if not df.empty:
        df['ts']=pd.to_datetime(df.ts,utc=True)
    return df

def _time_bucket(tod):
    tod=float(tod)
    if tod < 10*60: return 'OPENING'
    if tod < 12*60: return 'MORNING'
    if tod < 14*60: return 'MIDDAY'
    if tod < 15*60+30: return 'AFTERNOON'
    return 'CLOSING'

def intraday_prediction(ticker):
    df=_intraday_load(ticker)
    # V1.8.20: keep on-demand collection, but require enough history for robust time conditioning.
    if len(df)<60:
        try:
            fresh=fetch_chart(ticker,CFGD.get('intraday_period_days',60),CFGD.get('intraday_interval','30m'))
            if not fresh.empty:
                save_intraday(fresh,ticker); df=_intraday_load(ticker)
        except Exception:
            pass
    if len(df)<60:return pd.DataFrame()
    x=df.copy(); x['wib_hour']=(x.ts.dt.hour+7)%24; x['wib_min']=x.ts.dt.minute; x['tod']=x.wib_hour*60+x.wib_min
    x['ret1']=x.close.pct_change(fill_method=None); x['vol20']=x.volume.rolling(20).mean(); x['vol_ratio']=x.volume/x.vol20.replace(0,np.nan)
    x['ret30']=x.close.shift(-1)/x.close-1; x['ret60']=x.close.shift(-2)/x.close-1
    x['day']=x.ts.dt.date; x['day_end']=x.groupby('day').close.transform('last'); x['reteod']=x.day_end/x.close-1
    x=x.dropna(subset=['ret30','ret60','reteod','vol_ratio']).copy()
    if len(x)<30:return pd.DataFrame()
    now=df.iloc[-1]; now_ts=now.ts; now_tod=((now_ts.hour+7)%24)*60+now_ts.minute
    current_bucket=_time_bucket(now_tod)
    current_ret=(df.close.iloc[-1]/df.close.iloc[-2]-1) if len(df)>1 and df.close.iloc[-2] else 0.0
    basevol=float(df.volume.tail(20).mean()) if len(df) else 0.0
    current_vol=(float(df.volume.iloc[-1])/basevol) if basevol>0 else 1.0
    # V1.8.20: compare with historical observations in the same intraday window first,
    # then relax to +/-60 minutes only when the bucket lacks enough samples.
    x['tod_bucket']=x['tod'].map(_time_bucket)
    same=x[x.tod_bucket==current_bucket].copy()
    if len(same)>=20:
        pool=same
        window_mode='SAME_TIME_BUCKET'
    else:
        x['tod_dist']=(x.tod-now_tod).abs()
        pool=x[x.tod_dist<=60].copy()
        window_mode='±60_MIN'
        if len(pool)<20:
            pool=x.copy(); window_mode='ALL_INTRADAY'
    pool['tod_dist']=(pool.tod-now_tod).abs()
    pool['sim']=np.abs(pool.ret1-current_ret)+0.25*np.abs(np.log(pool.vol_ratio.clip(lower=0.1))-np.log(max(current_vol,0.1)))+0.002*pool.tod_dist
    pool=pool.sort_values('sim').head(min(250,len(pool)))
    rows=[]
    threshold=float(CFGD.get('time_return_threshold',0.002))
    for name,col in [('30m','ret30'),('60m','ret60'),('EOD','reteod')]:
        z=pool[col].dropna()
        up=float((z>threshold).mean()); down=float((z<-threshold).mean()); side=max(0.0,1-up-down); mean=float(z.mean())
        samples=len(z)
        edge=up-down
        # V1.8.20.1: calibrated confidence. The previous version used only
        # sample count, which made 30m/60m/EOD confidence nearly identical even
        # when their directional evidence was very different. Confidence now
        # reflects three independent signals: sample reliability, directional
        # separation, and concentration of the dominant outcome. It is an
        # evidence-strength score, not a guarantee that the forecast is correct.
        sample_rel=min(1.0, samples/120.0)
        direction_sep=min(1.0, abs(edge)/0.35)
        dominant=max(up,down,side)
        concentration=np.clip((dominant-1/3)/(2/3),0.0,1.0)
        conf=float(np.clip(0.30+0.30*sample_rel+0.25*direction_sep+0.15*concentration,0.30,0.90))
        if conf>=0.55 and edge>=0.10 and mean>0:
            status='ACTIVE'
        elif conf>=0.45 and edge>=0.03 and mean>0:
            status='DEVELOPING'
        elif edge<=-0.10 or (mean<0 and down>up):
            status='WAIT'
        else:
            status='NEUTRAL'
        rows.append({'horizon':name,'samples':samples,'p_up':up,'p_sideways':side,'p_down':down,'mean_return':mean,'confidence':conf,'data_status':'READY' if samples>=30 else 'LIMITED','time_bucket':current_bucket,'window_mode':window_mode,'entry_status':status,'edge':edge})
    return pd.DataFrame(rows)


def intraday_time_map(ticker):
    """Historical 30-minute decision map for the selected opportunity.

    The map uses the same 30m intraday history as Time Intelligence, grouped by
    WIB clock slots. It is an evidence map, not a promise of what will happen
    at that exact minute. Session breaks and Friday's different schedule are
    represented explicitly.
    """
    df=_intraday_load(str(ticker))
    if len(df)<60:
        try:
            fresh=fetch_chart(str(ticker),CFGD.get('intraday_period_days',60),CFGD.get('intraday_interval','30m'))
            if not fresh.empty:
                save_intraday(fresh,str(ticker)); df=_intraday_load(str(ticker))
        except Exception:
            pass
    if len(df)<30:
        return pd.DataFrame()
    x=df.copy().sort_values('ts').reset_index(drop=True)
    local=x['ts'].dt.tz_convert('Asia/Jakarta')
    x['tod']=local.dt.hour*60+local.dt.minute
    x['day']=local.dt.date
    x['next_tod']=x['tod'].shift(-1)
    x['next_day']=x['day'].shift(-1)
    x['next_close']=x['close'].shift(-1)
    x['ret30']=np.where((x['next_day']==x['day']) & ((x['next_tod']-x['tod'])<=45), x['next_close']/x['close']-1, np.nan)
    x['day_end']=x.groupby('day')['close'].transform('last')
    x['reteod']=x['day_end']/x['close']-1
    x['vol20']=x['volume'].rolling(20).mean()
    x['vol_ratio']=x['volume']/x['vol20'].replace(0,np.nan)
    x=x.dropna(subset=['close','vol_ratio']).copy()
    threshold=float(CFGD.get('time_return_threshold',0.002))
    # Normal trading checkpoints. 12:00 is a session boundary on Mon-Thu;
    # Friday's second session begins at 14:00, so the UI marks that separately.
    slots=[
        (9*60,'09:00','Sesi I'),(9*60+30,'09:30','Sesi I'),(10*60,'10:00','Sesi I'),
        (10*60+30,'10:30','Sesi I'),(11*60,'11:00','Sesi I'),(11*60+30,'11:30','Sesi I'),
        (12*60,'12:00','BREAK'),(13*60+30,'13:30','Sesi II'),(14*60,'14:00','Sesi II'),
        (14*60+30,'14:30','Sesi II'),(15*60,'15:00','Sesi II'),(15*60+30,'15:30','Sesi II'),
        (15*60+50,'15:50','PRE-CLOSING'),(16*60,'16:00','CLOSING')
    ]
    rows=[]
    for mins,label,session in slots:
        # Break / closing rows are schedule markers, not predictive bars.
        if session in ('BREAK','PRE-CLOSING','CLOSING'):
            rows.append({'time':label,'session':session,'horizon':'MARKET','samples':None,'up_pct':None,'sideways_pct':None,'down_pct':None,'mean_return_pct':None,'confidence_pct':None,'status':'CHECKPOINT','note':'Pantau perubahan kondisi / harga penutupan.'})
            continue
        same=x[x.tod==mins].copy()
        if session=='Sesi II':
            # Friday 13:30 is not a regular-session checkpoint. Keep it visible
            # but do not manufacture statistics from another clock slot.
            same_day=pd.Series(local.dt.dayofweek==4,index=x.index)
            if label=='13:30':
                same=same[~same_day.loc[same.index]]
        z=same.ret30.dropna()
        # At the final regular checkpoint use the remaining session/EOD move.
        horizon='30m'
        if label=='15:30':
            z=same.reteod.dropna(); horizon='EOD'
        if len(z)<8:
            rows.append({'time':label,'session':session,'horizon':horizon,'samples':int(len(z)),'up_pct':None,'sideways_pct':None,'down_pct':None,'mean_return_pct':None,'confidence_pct':None,'status':'LIMITED','note':'Data historis slot belum cukup.'})
            continue
        up=float((z>threshold).mean()); down=float((z<-threshold).mean()); side=max(0.0,1-up-down); mean=float(z.mean()); edge=up-down
        sample_rel=min(1.0,len(z)/60.0); sep=min(1.0,abs(edge)/0.35); dominant=max(up,down,side); concentration=float(np.clip((dominant-1/3)/(2/3),0,1))
        conf=float(np.clip(0.30+0.30*sample_rel+0.25*sep+0.15*concentration,0.30,0.90))
        if conf>=0.55 and edge>=0.10 and mean>0: status='ACTIVE'
        elif conf>=0.45 and edge>=0.03 and mean>0: status='DEVELOPING'
        elif edge<=-0.10 or (mean<0 and down>up): status='WAIT'
        else: status='NEUTRAL'
        rows.append({'time':label,'session':session,'horizon':horizon,'samples':len(z),'up_pct':up*100,'sideways_pct':side*100,'down_pct':down*100,'mean_return_pct':mean*100,'confidence_pct':conf*100,'status':status,'note':f'Edge {edge*100:+.1f}%; estimasi historis slot.'})
    return pd.DataFrame(rows)

def read_universe_registry(limit=200):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT * FROM universe_registry ORDER BY status, confidence DESC, ticker LIMIT ?',con,params=(int(limit),)); con.close(); return d
    except Exception:return pd.DataFrame()

def registry_summary():
    d=read_universe_registry(5000)
    if d.empty:return {'total':0,'direct':0,'resolved':0,'not_found':0,'unresolved':0}
    s=d.status.astype(str)
    return {'total':len(d),'direct':int((s=='DIRECT').sum()),'resolved':int((s=='RESOLVED').sum()),'not_found':int((s=='PROVIDER_NOT_FOUND').sum()),'unresolved':int((s=='UNRESOLVED').sum())}



def read_hold_intelligence(limit=10):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT * FROM hold_intelligence ORDER BY hold_score DESC LIMIT ?',con,params=(int(limit),)); con.close(); return d
    except Exception: return pd.DataFrame()

def read_dynamic_opportunities(limit=10):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT * FROM dynamic_opportunities ORDER BY rank LIMIT ?',con,params=(int(limit),)); con.close(); return d
    except Exception:
        return pd.DataFrame()

def read_change_alerts(limit=20):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT generated_at,ticker,company,severity,alert_type,title,detail,old_value,new_value FROM alert_history ORDER BY id DESC LIMIT ?',con,params=(int(limit),)); con.close(); return d
    except Exception:
        return pd.DataFrame()


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

def _snapshot_status():
    snap=state_get('snapshot_ready_at')
    source=state_get('snapshot_source') or ''
    return snap,source


def read_outlook():
    try:
        con=dbcon(); r=con.execute('SELECT generated_at,next_session,market_status,buy_count,sell_count,near_count,wait_count,summary FROM outlook WHERE id=1').fetchone(); con.close()
        if not r: return None
        return dict(generated_at=r[0],next_session=r[1],market_status=r[2],buy_count=r[3],sell_count=r[4],near_count=r[5],wait_count=r[6],summary=r[7])
    except Exception: return None


def read_model_validation():
    p=os.path.join(DB_DIR,'model_validation.json')
    if not os.path.exists(p): return pd.DataFrame(),pd.DataFrame()
    try:
        with open(p,'r',encoding='utf-8') as f: j=json.load(f)
        return pd.DataFrame(j.get('summary',[])),pd.DataFrame(j.get('folds',[]))
    except Exception:return pd.DataFrame(),pd.DataFrame()


def _discover_exact_ticker(query):
    """Discover an exact IDX ticker that is missing from the local universe.

    This is deliberately stricter than fuzzy resolution: the provider symbol must
    be exactly <TICKER>.JK. A near match is never allowed to masquerade as the
    requested ticker.
    """
    ticker=_norm_text(query).replace(' ','')
    if not re.fullmatch(r'[A-Z0-9]{2,6}',ticker):
        return None
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        try:
            r=requests.get(
                f'https://{host}/v1/finance/search',
                params={'q':ticker,'quotesCount':20,'newsCount':0},
                timeout=CFGD.get('request_timeout_seconds',8),
                headers={'User-Agent':CFGD['user_agent']}
            )
            r.raise_for_status()
            quotes=(r.json() or {}).get('quotes') or []
            exact=[]
            for q in quotes:
                sym=str(q.get('symbol','')).upper()
                if sym != f'{ticker}.JK':
                    continue
                if str(q.get('quoteType','EQUITY')).upper()!='EQUITY':
                    continue
                name=q.get('longname') or q.get('shortname') or q.get('name') or ticker
                exact.append((sym,name))
            if exact:
                sym,name=exact[0]
                registry_upsert(ticker,name,sym,'DIRECT',1.0,'search_discovery','exact ticker discovered on demand')
                return {'ticker':ticker,'company':name,'match_score':1.0,'match_reason':'ticker exact • discovered'}
        except Exception:
            continue
    return None

def search_stock_matches(universe, query, limit=8):
    """Deterministic ticker/company search with strict exact-ticker discovery.

    Order: exact local ticker -> exact provider ticker discovery -> aliases ->
    company/prefix matches -> fuzzy candidates. Fuzzy matching is never allowed
    to replace a requested exact ticker.
    """
    q=_norm_text(query)
    if not q:
        return pd.DataFrame()
    universe = universe.copy() if universe is not None else pd.DataFrame(columns=['ticker','company'])
    if 'ticker' not in universe.columns:
        universe['ticker']=pd.Series(dtype=str)
    if 'company' not in universe.columns:
        universe['company']=universe['ticker']
    universe['ticker']=universe['ticker'].astype(str).str.upper().str.strip()
    universe['company']=universe['company'].astype(str)

    # 1) Local exact ticker always wins.
    local_exact=universe[universe.ticker.map(_norm_text)==q]
    if not local_exact.empty:
        u=local_exact.iloc[0]
        return pd.DataFrame([{'ticker':str(u.ticker).upper(),'company':str(u.company),
                              'match_score':1.0,'match_reason':'ticker exact'}])

    # 2) If the user typed a ticker-shaped query that is not in the local
    # universe, ask the provider for that exact symbol before fuzzy matching.
    discovered=_discover_exact_ticker(q)
    if discovered:
        return pd.DataFrame([discovered])

    aliases={'ATAM':'ANTM','ANTAM':'ANTM','ANEKA TAMBANG':'ANTM','BANK CENTRAL ASIA':'BBCA','BANK BCA':'BBCA'}
    rows=[]
    from difflib import SequenceMatcher
    alias_target=aliases.get(q,'')
    for _,u in universe.iterrows():
        t=str(u.ticker).upper().strip(); c=str(u.company)
        nt=_norm_text(t); nc=_norm_text(c)
        score=0.0; reason=''
        if alias_target and t==alias_target:
            score=0.995; reason=f'alias → {alias_target}'
        elif nt.startswith(q):
            score=0.965; reason='ticker prefix'
        elif q in nt:
            score=0.94; reason='ticker contains'
        elif q==nc:
            score=0.93; reason='company exact'
        elif q in nc:
            score=0.88; reason='company contains'
        else:
            score=max(SequenceMatcher(None,q,nt).ratio(),SequenceMatcher(None,q,nc).ratio()*0.90)
            reason='fuzzy'
        if score>=0.45:
            rows.append({'ticker':t,'company':c,'match_score':score,'match_reason':reason})
    if not rows:
        return pd.DataFrame()
    out=pd.DataFrame(rows)
    out=out.sort_values(['match_score','match_reason','ticker'],ascending=[False,True,True]).head(limit).reset_index(drop=True)
    return out


def read_full_decision_universe():
    """Load the complete latest decision universe for on-demand relative ranking.
    Unlike the dashboard preview, this intentionally does not LIMIT rows.
    """
    try:
        con=dbcon()
        d=pd.read_sql_query(
            "SELECT ticker, relative_rank_score, relative_percentile, ai_score, sector_score, relative_strength, jst_p_up, decision FROM opportunities WHERE relative_rank_score IS NOT NULL",
            con
        )
        con.close()
        return d
    except Exception:
        return pd.DataFrame()


def analyze_single_stock(ticker, company, universe, snapshot, model):
    """On-demand AI analysis for a searched stock.
    Reuses the active model and current market context without retraining or
    scanning the 957-stock universe again. Relative rank is calibrated against
    the latest stored snapshot when available.
    """
    con=dbcon()
    try:
        df=pd.read_sql_query('SELECT * FROM prices WHERE ticker=? ORDER BY date',con,params=(str(ticker).upper(),))
        ih=load_ihsg_daily(con)
    finally:
        con.close()
    if len(df)<60:
        return None
    z=indicators(df); r=z.iloc[-1]
    ih_latest=ih.iloc[-1].to_dict() if not ih.empty else {}
    ihsg_r1=float(ih_latest.get('ihsg_ret1',0) or 0)
    ihsg_r5=float(ih_latest.get('ihsg_ret5',0) or 0)
    ihsg_r10=float(ih_latest.get('ihsg_ret10',0) or 0)
    ihsg_gap=float(ih_latest.get('ihsg_ma20_gap',0) or 0)
    ihsg_vol=float(ih_latest.get('ihsg_vol20',0) or 0)
    regime,market_score=market_regime(ih)
    feat=dict(r)
    feat.update({'ihsg_ret1':ihsg_r1,'ihsg_ret5':ihsg_r5,'ihsg_ret10':ihsg_r10,'ihsg_ma20_gap':ihsg_gap,'ihsg_vol20':ihsg_vol,
                 'rel_ret1':float(r.ret1)-ihsg_r1 if pd.notna(r.ret1) else np.nan,
                 'rel_ret5':float(r.ret5)-ihsg_r5 if pd.notna(r.ret5) else np.nan,
                 'rel_ret10':float(r.ret10)-ihsg_r10 if pd.notna(r.ret10) else np.nan})
    f=np.array([[feat[k] for k in FEATURES]],dtype=float)
    if model is not None and np.all(np.isfinite(f)):
        prob=model.predict_proba(f)[0]; p_down,p_side,p_up=[float(x) for x in prob]
        signal=CLASS_NAMES[int(np.argmax(prob))]; conf=float(np.max(prob))
    else:
        p_down=p_side=p_up=np.nan; signal='N/A'; conf=np.nan
    sec=sector_proxy(company,ticker)
    sec_score=50.0
    if snapshot is not None and not snapshot.empty and 'sector_score' in snapshot.columns:
        ss=pd.to_numeric(snapshot.loc[snapshot.sector.astype(str)==sec,'sector_score'],errors='coerce').dropna()
        if not ss.empty: sec_score=float(ss.mean())
    base=50.0
    base+=max(-10,min(10,(float(r.rsi14)-50)*.25)) if pd.notna(r.rsi14) else 0
    base+=(12 if r.ma20>r.ma50 else -12) if pd.notna(r.ma20) and pd.notna(r.ma50) else 0
    base+=(10 if r.macd>r.macd_signal else -10) if pd.notna(r.macd) and pd.notna(r.macd_signal) else 0
    base+=max(-5,min(5,(float(r.vol_ratio)-1)*5)) if pd.notna(r.vol_ratio) else 0
    base+=max(-8,min(8,float(r.ret5)*100)) if pd.notna(r.ret5) else 0
    base=float(np.clip(base,0,100))
    rel_strength=float(np.clip(50+(float(r.ret5)-ihsg_r5)*180,0,100)) if pd.notna(r.ret5) else 50.0
    score=float(np.clip(.32*base+.28*(p_up*100 if np.isfinite(p_up) else base)+.15*market_score+.15*sec_score+.10*rel_strength,0,100))
    trend='BULLISH' if pd.notna(r.ma20) and pd.notna(r.ma50) and r.ma20>r.ma50 else 'BEARISH'
    atr=float(r.atr14) if pd.notna(r.atr14) else np.nan; close=float(r.close)
    atr_pct=float(r.atr_pct) if pd.notna(r.atr_pct) else 0.0; vol_ratio=float(r.vol_ratio) if pd.notna(r.vol_ratio) else 1.0
    risk=float(np.clip(35+(atr_pct*100*10)+(8 if r.rsi14>75 or r.rsi14<25 else 0)+(8 if vol_ratio>2.5 else 0),0,100))
    risk_label='LOW' if risk<40 else ('MEDIUM' if risk<65 else 'HIGH')
    if pd.notna(atr) and atr>0:
        recent_low=float(z['low'].tail(20).min())
        support_stop=max(close-2.0*atr,recent_low-0.25*atr)
        sl=float(min(close-1.0*atr,support_stop)); risk_per_share=max(close-sl,0.5*atr)
        entry_low=max(sl+0.75*risk_per_share,close-0.50*atr); entry_high=close+0.10*atr
        tp1=close+1.50*risk_per_share; tp2=close+2.50*risk_per_share
        rr1=(tp1-close)/risk_per_share if risk_per_share>0 else np.nan; rr2=(tp2-close)/risk_per_share if risk_per_share>0 else np.nan
        invalidation=f'Close < {sl:.4f} or bullish conditions materially weaken'
    else:
        sl=entry_low=entry_high=tp1=tp2=rr1=rr2=np.nan; invalidation='Insufficient ATR history'
    # Compare against the complete latest stored decision universe, not the
    # dashboard's 100-row preview. This prevents a valid on-demand stock from
    # displaying misleading "0 / 957" ranks.
    rank_pct=np.nan; rank=None
    target=.55*score+.20*rel_strength+.15*sec_score+.10*(p_up*100 if np.isfinite(p_up) else 50)
    rank_universe=read_full_decision_universe()
    if not rank_universe.empty:
        existing=pd.to_numeric(rank_universe.relative_rank_score,errors='coerce').dropna().values
        if len(existing):
            rank_pct=float(np.mean(existing<=target))
            rank=int(1+np.sum(existing>target))
    elif snapshot is not None and not snapshot.empty and 'relative_rank_score' in snapshot.columns:
        existing=pd.to_numeric(snapshot.relative_rank_score,errors='coerce').dropna().values
        if len(existing):
            rank_pct=float(np.mean(existing<=target))
            rank=int(1+np.sum(existing>target))
    result={'ticker':ticker,'company':company,'close':close,'change_1d':float(r.ret1) if pd.notna(r.ret1) else np.nan,
            'rsi14':float(r.rsi14) if pd.notna(r.rsi14) else np.nan,'vol_ratio':vol_ratio,'trend':trend,'ai_score':score,
            'jst_p_down':p_down,'jst_p_sideways':p_side,'jst_p_up':p_up,'jst_signal':signal,'jst_confidence':conf,
            'market_score':market_score,'market_regime':regime,'sector':sec,'sector_score':sec_score,'relative_strength':rel_strength,
            'risk':risk_label,'risk_score':risk,'risk_per_share':risk_per_share,'entry_low':entry_low,'entry_high':entry_high,
            'stop_loss':sl,'target_1':tp1,'target_2':tp2,'risk_reward_1':rr1,'risk_reward_2':rr2,'invalidation':invalidation,
            'relative_rank_score':target,'relative_percentile':rank_pct,'rank':rank,
            'baseline_score':base,'price_vs_ma20':(close/float(r.ma20)-1) if pd.notna(r.ma20) and r.ma20 else np.nan,
            'price_vs_ma50':(close/float(r.ma50)-1) if pd.notna(r.ma50) and r.ma50 else np.nan,
            'high20_gap':float(r.high20_gap) if pd.notna(r.high20_gap) else np.nan,
            'low20_gap':float(r.low20_gap) if pd.notna(r.low20_gap) else np.nan}
    if np.isfinite(rank_pct):
        ds,score_dec,reason=classify_decision(pd.Series(result))
        result.update({'decision':ds,'decision_score':score_dec,'decision_reason':reason})
    else:
        result.update({'decision':'ANALYSIS','decision_score':score,'decision_reason':'On-demand analysis; relative snapshot rank unavailable.'})
    return result

def read_stock_prices(ticker, limit=260):
    try:
        con=dbcon(); d=pd.read_sql_query('SELECT date,open,high,low,close,volume,source,updated_at FROM prices WHERE ticker=? ORDER BY date DESC LIMIT ?',con,params=(str(ticker).upper(),int(limit))); con.close()
        if d.empty:return d
        d=d.sort_values('date').reset_index(drop=True)
        for c in ['open','high','low','close','volume']:
            d[c]=pd.to_numeric(d[c],errors='coerce')
        return d.dropna(subset=['close'])
    except Exception:return pd.DataFrame()


def stock_deep_link(ticker):
    """Build a same-app navigation link for any ticker."""
    t=str(ticker).upper().strip()
    return f'?ticker={t}'

def clickable_ticker_frame(df, ticker_col='ticker', company_col='company'):
    """Prepare ticker/company columns as clickable deep-analysis links."""
    d=df.copy()
    if ticker_col in d.columns:
        d[ticker_col]=d[ticker_col].astype(str).map(lambda t: stock_deep_link(t))
    return d

def _open_ticker_mobile(ticker):
    """Touch-safe same-app navigation for Android WebView and desktop."""
    st.query_params['ticker']=str(ticker).upper().replace('.JK','').strip()


def render_clickable_dataframe(df, cols, round_digits=None):
    """Mobile-first table: every ticker is a real HTML touch link."""
    import html
    from urllib.parse import quote
    d=df[cols].copy()
    if round_digits is not None:
        try: d=d.round(round_digits)
        except Exception: pass
    def fmt(v):
        if pd.isna(v): return '—'
        if isinstance(v,(float,np.floating)):
            return f'{float(v):,.{int(round_digits or 2)}f}'.rstrip('0').rstrip('.')
        return str(v)
    head=''.join(f'<th>{html.escape(str(c))}</th>' for c in d.columns)
    rows=[]
    for _,r in d.iterrows():
        cells=[]
        for c in d.columns:
            if str(c).lower() in ('ticker','kode saham','symbol'):
                t=str(r[c]).upper().replace('.JK','').strip()
                cell=f'<a class="ticker-link" href="?ticker={quote(t)}" target="_self">{html.escape(t)}</a>'
            else: cell=html.escape(fmt(r[c]))
            cells.append(f'<td>{cell}</td>')
        rows.append('<tr>'+''.join(cells)+'</tr>')
    css='<style>.ticker-table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid rgba(128,128,128,.25);border-radius:14px;margin:.35rem 0 .8rem}.ticker-table{border-collapse:collapse;width:max-content;min-width:100%;font-size:.92rem}.ticker-table th{background:#242730;color:#d7d9df;text-align:left;padding:10px 12px;white-space:nowrap;border-bottom:1px solid rgba(128,128,128,.25)}.ticker-table td{padding:10px 12px;white-space:nowrap;border-bottom:1px solid rgba(128,128,128,.18)}.ticker-link{color:#42a5f5!important;text-decoration:none!important;font-weight:750;display:inline-block;min-width:48px;min-height:30px;line-height:30px}@media(max-width:700px){.ticker-table{font-size:.88rem}.ticker-table th,.ticker-table td{padding:9px 10px}}</style>'
    st.markdown(css+f'<div class="ticker-table-wrap"><table class="ticker-table"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>',unsafe_allow_html=True)

def render_smart_dataframe(df, width='stretch', hide_index=True, round_digits=None):
    """Use touch-safe ticker links whenever a table contains a ticker/symbol column."""
    if df is None:
        return
    d=df.copy()
    ticker_cols=[c for c in d.columns if str(c).lower() in ('ticker','kode saham','symbol')]
    if ticker_cols:
        render_clickable_dataframe(d,d.columns.tolist(),round_digits)
    else:
        if round_digits is not None:
            try: d=d.round(round_digits)
            except Exception: pass
        st.dataframe(d,width=width,hide_index=hide_index)

def intraday_checkpoint_status():
    """Return current IDX regular-session checkpoint in WIB."""
    now=datetime.now(ZoneInfo('Asia/Jakarta')).replace(tzinfo=None)
    wd=now.weekday(); hm=now.hour*60+now.minute
    if wd>=5:
        return 'WEEKEND','—','Tidak ada sesi reguler'
    morning_end=11*60+30 if wd==4 else 12*60
    afternoon_start=14*60 if wd==4 else 13*60+30
    if hm < 9*60:
        return 'PRE-MARKET','09:00','Menunggu pembukaan'
    if hm <= morning_end:
        slots=[9*60,9*60+30,10*60,10*60+30,11*60,11*60+30] if wd==4 else [9*60,9*60+30,10*60,10*60+30,11*60,11*60+30,12*60]
        prev=max([x for x in slots if x<=hm],default=9*60)
        nxt=next((x for x in slots if x>hm),None)
        label=f'{prev//60:02d}:{prev%60:02d}'
        next_label=f'{nxt//60:02d}:{nxt%60:02d}' if nxt is not None else ('14:00' if wd==4 else '13:30')
        return 'SESI I',next_label,f'Checkpoint aktif {label}'
    if hm < afternoon_start:
        return 'BREAK',f'{afternoon_start//60:02d}:{afternoon_start%60:02d}','Istirahat pasar'
    if hm < 15*60+50:
        slots=[14*60,14*60+30,15*60,15*60+30] if wd==4 else [13*60+30,14*60,14*60+30,15*60,15*60+30]
        prev=max([x for x in slots if x<=hm],default=afternoon_start)
        nxt=next((x for x in slots if x>hm),None)
        label=f'{prev//60:02d}:{prev%60:02d}'
        next_label=f'{nxt//60:02d}:{nxt%60:02d}' if nxt is not None else '15:50'
        return 'SESI II',next_label,f'Checkpoint aktif {label}'
    if hm <= 16*60:
        return 'PRE-CLOSING', '16:00', 'Menjelang penutupan'
    if hm <= 16*60+15:
        return 'POST-TRADING','—','Post-trading'
    return 'AFTER-MARKET','09:00', 'Menunggu sesi berikutnya'

def stock_search_panel(universe, res, forced_ticker=None):
    """V1.8.18 Search & On-Demand Deep Analysis.
    Reuses the same cached prices, registry and decision snapshot as the engine.
    """
    st.subheader('🔎 Stock Search & Deep Analysis')
    st.caption('Cari ticker, nama perusahaan, atau alias. Search membaca data engine yang sudah tersedia dan tidak menjalankan retraining/scan 957 saham ulang.')
    default_q=str(forced_ticker or '')
    q=st.text_input('Cari saham',value=default_q,placeholder='Contoh: ANTM, ATAM, Aneka Tambang, BBCA...',key='stock_search_query')
    if not q.strip():
        st.info('Ketik ticker atau nama saham untuk membuka analisis detail.')
        return
    matches=search_stock_matches(universe,q,8)
    if matches.empty:
        st.warning(f'Tidak menemukan kandidat untuk “{q}”. Pastikan ticker/nama sesuai universe yang tersedia.')
        return
    labels=[f"{r.ticker} • {r.company} • match {r.match_score*100:.0f}%" for _,r in matches.iterrows()]
    forced_norm=str(forced_ticker or '').upper().replace('.JK','').strip()
    if forced_norm and any(str(x).split(' • ')[0].upper()==forced_norm for x in labels):
        selected_idx=next(i for i,x in enumerate(labels) if str(x).split(' • ')[0].upper()==forced_norm)
    else:
        selected_label=st.selectbox('Hasil pencarian',labels,key='stock_search_selection')
        selected_idx=labels.index(selected_label)
    selected_ticker=str(matches.iloc[selected_idx].ticker)
    company=str(matches.iloc[selected_idx].company)
    if forced_norm:
        st.success(f'📌 Saham aktif: **{selected_ticker} • {company}** • semua modul di bawah mengikuti ticker ini.')
    reg=registry_get(selected_ticker) or {}
    row=res[res.ticker.astype(str).str.upper()==selected_ticker].head(1) if res is not None and not res.empty else pd.DataFrame()
    prices=read_stock_prices(selected_ticker,260)
    # New listings may be discoverable before the scheduled universe collector
    # has seen them. Fetch daily history on demand so Search is useful immediately.
    if prices.empty:
        try:
            d,status,resolved,err=fetch_chart_recover(selected_ticker,CFGD.get('daily_period_days',400),CFGD.get('daily_interval','1d'),company)
            if not d.empty:
                save_daily(d,selected_ticker)
                prices=read_stock_prices(selected_ticker,260)
                if resolved:
                    registry_upsert(selected_ticker,company,resolved,status,1.0 if status=='DIRECT' else 0.85,'search_on_demand','daily history fetched on demand')
        except Exception:
            pass

    st.markdown(f'## {selected_ticker} · {company}')
    meta=[]
    if reg.get('provider_symbol'): meta.append(f"Provider: {reg.get('provider_symbol')}")
    if reg.get('status'): meta.append(f"Status: {reg.get('status')}")
    if reg.get('last_checked'): meta.append(f"Checked: {reg.get('last_checked')}")
    st.caption(' • '.join(meta) if meta else 'Ticker registry belum memiliki metadata tambahan.')

    if prices.empty:
        st.warning('Belum ada data harga yang cukup untuk saham ini pada database engine. Search tetap mengenali sahamnya, tetapi AI tidak akan mengarang angka.')
        return

    z=indicators(prices); r=z.iloc[-1]
    # V1.8.18: every searchable stock gets the same active-model intelligence
    # on demand, even when it was outside the last 100-stock decision snapshot.
    on_demand=None
    if row.empty:
        try:
            on_demand=analyze_single_stock(selected_ticker,company,universe,res,load_active_model())
            if on_demand is not None:
                row=pd.DataFrame([on_demand])
        except Exception as e:
            on_demand=None
    close=float(r.close); prev=float(z.close.iloc[-2]) if len(z)>1 else np.nan
    ch=(close/prev-1)*100 if np.isfinite(prev) and prev else np.nan
    latest_source=str(prices.source.iloc[-1]) if 'source' in prices.columns else 'engine'
    latest_updated=str(prices.updated_at.iloc[-1]) if 'updated_at' in prices.columns else ''

    p1,p2,p3,p4,p5=st.columns(5)
    p1.metric('Harga terakhir',f'Rp {close:,.0f}')
    p2.metric('Perubahan 1D',f'{ch:+.2f}%' if np.isfinite(ch) else '—')
    p3.metric('Volume',f'{float(r.volume):,.0f}' if pd.notna(r.volume) else '—')
    p4.metric('MA20',f'{float(r.ma20):,.0f}' if pd.notna(r.ma20) else '—')
    p5.metric('RSI 14',f'{float(r.rsi14):.1f}' if pd.notna(r.rsi14) else '—')
    st.caption(f'Data harga terakhir: {r.date} • source={latest_source} • updated={latest_updated}')

    # V1.8.28: Universal Stock Intelligence. One compact status layer summarizes
    # rank, opportunity, session and next checkpoint for the currently selected ticker.
    session_name,next_checkpoint,session_note=intraday_checkpoint_status()
    dyn_all=read_dynamic_opportunities(2000)
    dyn_row=dyn_all[dyn_all.ticker.astype(str).str.upper()==selected_ticker] if not dyn_all.empty and 'ticker' in dyn_all.columns else pd.DataFrame()
    hold_all=read_hold_intelligence(2000)
    hold_row=hold_all[hold_all.ticker.astype(str).str.upper()==selected_ticker] if not hold_all.empty and 'ticker' in hold_all.columns else pd.DataFrame()
    hub1,hub2,hub3,hub4,hub5,hub6=st.columns(6)
    hub1.metric('Relative Rank',f"{int(row.iloc[0]['rank'])} / {len(universe)}" if not row.empty and 'rank' in row.columns and pd.notna(row.iloc[0]['rank']) else '—')
    hub2.metric('Opportunity Rank',f"#{int(dyn_row.iloc[0]['rank'])}" if not dyn_row.empty and pd.notna(dyn_row.iloc[0]['rank']) else '—')
    hub3.metric('Entry Window',str(dyn_row.iloc[0]['entry_status']) if not dyn_row.empty and 'entry_status' in dyn_row.columns else '—')
    hub4.metric('Hold State',str(hold_row.iloc[0]['state']) if not hold_row.empty and 'state' in hold_row.columns else '—')
    hub5.metric('Sesi',session_name)
    hub6.metric('Next Checkpoint',next_checkpoint)
    st.info(f'🧭 **Monitoring {selected_ticker}** • {session_note} • checkpoint berikutnya **{next_checkpoint} WIB**. Semua modul di halaman ini menggunakan ticker yang sama.')

    # V1.8.28: Universal Stock Intelligence unified snapshot. This is the
    # single source-of-truth layer for the selected ticker across rank,
    # opportunity, entry, risk, hold, overnight and market context.
    st.markdown('### 🧠 Universal Stock Intelligence • Unified Snapshot')
    ur = row.iloc[0] if not row.empty else None
    dr = dyn_row.iloc[0] if not dyn_row.empty else None
    hr = hold_row.iloc[0] if not hold_row.empty else None
    def _val(obj, key, default=np.nan):
        try:
            if obj is None:
                return default
            if hasattr(obj, 'get'):
                v = obj.get(key, default)
            elif isinstance(obj, dict):
                v = obj.get(key, default)
            else:
                v = getattr(obj, key)
            return v if v is not None else default
        except Exception:
            return default
    ai_score = _val(ur, 'ai_score') if ur is not None else np.nan
    decision = str(_val(ur, 'decision', '—')) if ur is not None else '—'
    opp_score = _val(dr, 'opportunity_score') if dr is not None else np.nan
    opp_state = str(_val(dr, 'state', '—')) if dr is not None else '—'
    entry_state = str(_val(dr, 'entry_status', '—')) if dr is not None else '—'
    risk_state = str(_val(ur, 'risk', '—')) if ur is not None else '—'
    hold_score = _val(hr, 'hold_score') if hr is not None else np.nan
    overnight = str(_val(hr, 'overnight_state', '—')) if hr is not None else '—'
    next_session = str(_val(hr, 'next_session_bias', '—')) if hr is not None else '—'
    ihsg_regime = str(_val(dr, 'market_regime', _val(ur, 'market_regime', '—'))) if (dr is not None or ur is not None) else '—'
    ihsg_score = _val(dr, 'market_score', _val(ur, 'market_score', np.nan)) if (dr is not None or ur is not None) else np.nan
    ihsg_alignment = _val(dr, 'ihsg_alignment', np.nan) if dr is not None else np.nan
    rank_value = _val(ur, 'rank') if ur is not None else np.nan
    opp_rank_value = _val(dr, 'rank') if dr is not None else np.nan

    u1,u2,u3,u4,u5,u6=st.columns(6)
    u1.metric('AI Score', f'{float(ai_score):.1f}/100' if pd.notna(ai_score) else '—')
    u2.metric('Decision', decision)
    u3.metric('Relative Rank', f'#{int(rank_value)} / {len(universe)}' if pd.notna(rank_value) else '—')
    u4.metric('Opportunity', f'{float(opp_score):.1f}' if pd.notna(opp_score) else '—')
    u5.metric('Opp. Rank', f'#{int(opp_rank_value)}' if pd.notna(opp_rank_value) else '—')
    u6.metric('Entry Window', entry_state)

    u7,u8,u9,u10,u11,u12=st.columns(6)
    u7.metric('Opportunity State', opp_state)
    u8.metric('Risk', risk_state)
    u9.metric('Hold Score', f'{float(hold_score):.1f}' if pd.notna(hold_score) else '—')
    u10.metric('Overnight', overnight)
    u11.metric('Next Session', next_session)
    u12.metric('IHSG Regime', ihsg_regime)

    market_bits=[]
    if pd.notna(ihsg_score): market_bits.append(f'IHSG Score {float(ihsg_score):.1f}')
    if pd.notna(ihsg_alignment): market_bits.append(f'Alignment {float(ihsg_alignment):.1f}')
    market_bits.append(f'Risk {risk_state}')
    st.caption(f'Universal state: **{selected_ticker}** → AI → Relative Rank → Opportunity → Entry Window → Risk → Hold → Overnight → Next Session. ' + ' • '.join(market_bits))

    st.markdown('### 🚨 Smart Monitoring & Alert Engine')
    alerts=read_change_alerts(30)
    if alerts.empty:
        st.info('Belum ada perubahan material pada snapshot sebelumnya. Sistem sedang membangun baseline dan tidak mengirim alert palsu.')
    else:
        a=alerts[alerts['ticker'].astype(str).str.upper()==selected_ticker].copy()
        if a.empty:
            st.success('Tidak ada perubahan material terbaru untuk ticker ini. Monitoring tetap aktif.')
        else:
            for _,ar in a.head(8).iterrows():
                sev=str(ar.severity); msg=f"**{ar.title}** • {ar.detail}"
                if sev=='HIGH': st.error(f'🔴 HIGH • {msg}')
                elif sev=='MEDIUM': st.warning(f'🟠 MEDIUM • {msg}')
                else: st.info(f'🔵 LOW • {msg}')
        with st.expander('Lihat alert terbaru seluruh universe',expanded=False):
            showa=alerts.rename(columns={'generated_at':'Waktu','ticker':'Ticker','company':'Company','severity':'Priority','title':'Alert','detail':'Perubahan','old_value':'Sebelum','new_value':'Sesudah'})
            render_smart_dataframe(showa[['Waktu','Ticker','Company','Priority','Alert','Perubahan','Sebelum','Sesudah']])

    st.markdown('### 🛡️ Hold / Overnight Detail')
    if hr is None:
        st.info('Hold Intelligence belum memiliki record untuk ticker ini pada snapshot terakhir.')
    else:
        hc1,hc2,hc3,hc4,hc5,hc6=st.columns(6)
        hc1.metric('Hold Score', f'{float(_val(hr,"hold_score")):.1f}' if pd.notna(_val(hr,'hold_score')) else '—')
        hc2.metric('Hold State', str(_val(hr,'state','—')))
        hc3.metric('Hold Horizon', str(_val(hr,'hold_horizon','—')))
        hc4.metric('Overnight', str(_val(hr,'overnight_state','—')))
        hc5.metric('Next Session', str(_val(hr,'next_session_bias','—')))
        hc6.metric('P(Hold)', f'{float(_val(hr,"p_hold"))*100:.1f}%' if pd.notna(_val(hr,'p_hold')) else '—')
        st.caption(f"P(UP) {float(_val(hr,'p_up'))*100:.1f}% • P(DOWN) {float(_val(hr,'p_down'))*100:.1f}% • Confidence {float(_val(hr,'confidence'))*100:.1f}%" if pd.notna(_val(hr,'p_up')) and pd.notna(_val(hr,'p_down')) and pd.notna(_val(hr,'confidence')) else 'Hold probabilities belum lengkap.')

    st.markdown('### 🗂️ Data Coverage')
    tm_coverage=intraday_time_map(selected_ticker)
    dc1,dc2,dc3,dc4=st.columns(4)
    dc1.metric('Technical', 'READY')
    dc2.metric('Intraday / Time Map', 'READY' if not tm_coverage.empty else 'LIMITED')
    dc3.metric('Fundamental', 'NOT CONNECTED')
    dc4.metric('News / Sentiment', 'NOT CONNECTED')
    st.caption('Fundamental dan News/Sentiment sengaja ditandai belum terhubung. Sistem tidak mengarang angka atau berita ketika sumber datanya belum tersedia.')

    st.markdown('### 📈 Market Chart')
    session_name,next_checkpoint,session_note=intraday_checkpoint_status()
    now_wib=datetime.now(ZoneInfo('Asia/Jakarta'))
    market_open=session_name in ['SESI I','SESI II','PRE-CLOSING','POST-TRADING']
    market_icon='🟢' if market_open else ('🟡' if session_name in ['PRE-MARKET','BREAK'] else '⚫')
    st.caption(f'{market_icon} {session_name} • {now_wib.strftime("%H:%M WIB")} • {session_note}')
    period=st.radio('Periode',['1D','1W','1M','3M','YTD','1Y','3Y','5Y'],index=0,horizontal=True,key=f'chart_period_{selected_ticker}',label_visibility='collapsed')
    default_chart_mode=1 if period=='1W' else 0
    chart_mode=st.radio('Tampilan',['Line','Candlestick'],index=default_chart_mode,horizontal=True,key=f'chart_mode_{selected_ticker}_{period}',label_visibility='collapsed')

    market=pd.DataFrame(); is_intraday=(period=='1D')
    if is_intraday:
        market=_intraday_load(selected_ticker)
        try:
            fresh=fetch_chart(selected_ticker,2,CFGD.get('intraday_interval','30m'))
            if not fresh.empty:
                save_intraday(fresh,selected_ticker); market=_intraday_load(selected_ticker)
        except Exception: pass
        if not market.empty:
            market=market.copy(); market['wib']=market['ts'].dt.tz_convert('Asia/Jakarta'); market['trade_date']=market['wib'].dt.date
            dates=sorted(market['trade_date'].dropna().unique())
            if dates: market=market[market['trade_date']==dates[-1]].copy()
            market['x']=market['wib'].dt.tz_localize(None)
    else:
        day_map={'1W':7,'1M':31,'3M':93,'YTD':370,'1Y':370,'3Y':1100,'5Y':1830}
        need_days=day_map[period]; long_prices=prices.copy()
        if period in ['YTD','1Y','3Y','5Y']:
            try:
                extra=fetch_chart(selected_ticker,need_days,'1d')
                if not extra.empty: long_prices=extra.copy()
            except Exception: pass
        base=long_prices.copy()
        if 'date' not in base.columns and 'ts' in base.columns: base['date']=base['ts']
        base['date']=pd.to_datetime(base['date'],errors='coerce')
        if period=='YTD': base=base[base['date']>=pd.Timestamp(now_wib.year,1,1)]
        else: base=base[base['date']>=pd.Timestamp.now()-pd.Timedelta(days=need_days)]
        market=base[[c for c in ['date','open','high','low','close','volume'] if c in base.columns]].copy(); market['x']=market['date']

    market=market.dropna(subset=['x','close']) if not market.empty else market
    if not market.empty:
        first=float(market['close'].iloc[0]); last=float(market['close'].iloc[-1]); delta=last-first
        period_ret=(last/first-1)*100 if first else np.nan
        hi=float(pd.to_numeric(market['high'] if 'high' in market else market['close'],errors='coerce').max())
        lo=float(pd.to_numeric(market['low'] if 'low' in market else market['close'],errors='coerce').min())
        chart_color='#10b981' if delta>=0 else '#ef4444'; arrow='↗' if delta>=0 else '↘'
        label_period='Hari Ini' if period=='1D' else {'1W':'1 Minggu Terakhir','1M':'1 Bulan Terakhir','3M':'3 Bulan Terakhir','YTD':'YTD','1Y':'1 Tahun Terakhir','3Y':'3 Tahun Terakhir','5Y':'5 Tahun Terakhir'}[period]
        st.markdown(f'<div style="margin:.15rem 0 .35rem"><div style="font-size:2.35rem;font-weight:800;line-height:1.05">Rp {last:,.0f}</div><div style="font-size:1.05rem;color:{chart_color};margin-top:.35rem">{arrow} {delta:+,.0f} ({period_ret:+.2f}%) <span style="color:#8b8d96">{label_period}</span></div></div>',unsafe_allow_html=True)
        try:
            import altair as alt
            plot=market[['x','close']+[c for c in ['open','high','low','volume'] if c in market.columns]].dropna(subset=['x','close']).copy()
            xaxis=alt.Axis(format='%H:%M' if is_intraday else '%d %b',labelAngle=0,labelOverlap=True,title=None)
            if chart_mode=='Candlestick' and all(c in plot.columns for c in ['open','high','low','close']):
                plot['direction']=np.where(plot['close']>=plot['open'],'UP','DOWN')
                clr=alt.Color('direction:N',scale=alt.Scale(domain=['UP','DOWN'],range=['#10b981','#ef4444']),legend=None)
                rules=alt.Chart(plot).mark_rule().encode(x=alt.X('x:T',axis=xaxis),y=alt.Y('low:Q',title=None,scale=alt.Scale(zero=False)),y2='high:Q',color=clr,tooltip=[alt.Tooltip('x:T',title='Waktu'),alt.Tooltip('open:Q',title='Open',format=',.0f'),alt.Tooltip('high:Q',title='High',format=',.0f'),alt.Tooltip('low:Q',title='Low',format=',.0f'),alt.Tooltip('close:Q',title='Close',format=',.0f')])
                bars=alt.Chart(plot).mark_bar(size=8).encode(x=alt.X('x:T',axis=xaxis),y=alt.Y('open:Q',title=None,scale=alt.Scale(zero=False)),y2='close:Q',color=clr,tooltip=[alt.Tooltip('x:T',title='Waktu'),alt.Tooltip('close:Q',title='Harga',format=',.0f')])
                price_chart=(rules+bars).properties(height=360)
            else:
                nearest=alt.selection_point(nearest=True,on='pointerover',fields=['x'],empty=False)
                base=alt.Chart(plot).encode(x=alt.X('x:T',axis=xaxis),y=alt.Y('close:Q',title=None,scale=alt.Scale(zero=False)))
                line=base.mark_line(strokeWidth=3,color=chart_color)
                points=base.mark_point(size=90,color=chart_color).encode(opacity=alt.condition(nearest,alt.value(1),alt.value(0)),tooltip=[alt.Tooltip('x:T',title='Waktu',format='%H:%M' if is_intraday else '%d %b %Y'),alt.Tooltip('close:Q',title='Harga',format=',.0f')]).add_params(nearest)
                rule=alt.Chart(plot).mark_rule(color='#777',strokeDash=[3,3]).encode(x='x:T').transform_filter(nearest)
                ref=alt.Chart(pd.DataFrame({'y':[first]})).mark_rule(color='#8b8d96',strokeDash=[6,5],opacity=.65).encode(y='y:Q')
                price_chart=(ref+line+points+rule).properties(height=360)
            st.altair_chart(price_chart,use_container_width=True)
        except Exception:
            st.line_chart(market[['x','close']].set_index('x'),width='stretch',height=360)
        last_fmt='%H:%M WIB' if is_intraday else '%d %b %Y'
        st.caption(f'Low Rp {lo:,.0f} • High Rp {hi:,.0f} • data terakhir {pd.Timestamp(market["x"].iloc[-1]).strftime(last_fmt)} • feed provider dapat terlambat dari broker realtime.')
    else:
        st.info('Data grafik belum tersedia untuk periode ini.')

    with st.expander('📊 Technical Analysis'):
        t1,t2,t3,t4,t5=st.columns(5)
        t1.metric('Trend','BULLISH' if pd.notna(r.ma20) and pd.notna(r.ma50) and r.ma20>r.ma50 else 'BEARISH')
        t2.metric('MA20',f'{float(r.ma20):,.0f}' if pd.notna(r.ma20) else '—'); t3.metric('MA50',f'{float(r.ma50):,.0f}' if pd.notna(r.ma50) else '—')
        t4.metric('RSI14',f'{float(r.rsi14):.1f}' if pd.notna(r.rsi14) else '—'); t5.metric('MACD',f'{float(r.macd):.4f}' if pd.notna(r.macd) else '—')
        if 'volume' in market.columns and pd.to_numeric(market['volume'],errors='coerce').notna().any():
            vol=market[['x','volume']].copy(); vol['volume']=pd.to_numeric(vol['volume'],errors='coerce'); vol=vol.dropna().set_index('x'); st.caption('Volume'); st.bar_chart(vol,width='stretch',height=120)

    st.markdown('### 🧠 AI Intelligence')
    if not row.empty:
        rr=row.iloc[0]
        ai=float(rr.ai_score); decision=str(rr.decision); pu=float(rr.jst_p_up)*100 if pd.notna(rr.jst_p_up) else np.nan; pdn=float(rr.jst_p_down)*100 if pd.notna(rr.jst_p_down) else np.nan; conf=float(rr.jst_confidence)*100 if pd.notna(rr.jst_confidence) else np.nan
        a,b,c,d=st.columns(4); a.metric('AI Score',f'{ai:.1f}/100'); b.metric('Decision',decision); c.metric('Probability UP',f'{pu:.1f}%' if np.isfinite(pu) else '—'); d.metric('Confidence',f'{conf:.1f}%' if np.isfinite(conf) else '—')
        st.info(f"Alasan keputusan: {rr.decision_reason}")
    else:
        st.warning('Saham dikenali, tetapi model on-demand belum dapat dihitung karena data historis/fitur belum cukup.')


    if not row.empty:
        rr=row.iloc[0]
        st.markdown('### 🛡️ Risk Engine')
        r1,r2,r3,r4,r5=st.columns(5)
        r1.metric('Risk',str(rr.risk))
        r2.metric('Entry',f"{float(rr.entry_low):,.0f}–{float(rr.entry_high):,.0f}" if pd.notna(rr.entry_low) and pd.notna(rr.entry_high) else '—')
        r3.metric('Stop Loss',f"{float(rr.stop_loss):,.0f}" if pd.notna(rr.stop_loss) else '—')
        r4.metric('Target 1',f"{float(rr.target_1):,.0f}" if pd.notna(rr.target_1) else '—')
        r5.metric('R/R T2',f"1:{float(rr.risk_reward_2):.2f}" if pd.notna(rr.risk_reward_2) else '—')
        st.caption(f"Invalidation: {rr.invalidation}")

        st.markdown('### ⏱️ Time Intelligence')
        tp=intraday_prediction(selected_ticker)
        if tp.empty:
            st.info('Belum ada data intraday yang cukup untuk menghitung 30m/60m/EOD secara historis.')
        else:
            show=tp.copy(); show['p_up']=(show['p_up']*100).round(1); show['p_sideways']=(show['p_sideways']*100).round(1); show['p_down']=(show['p_down']*100).round(1); show['mean_return']=(show['mean_return']*100).round(2); show['confidence']=(show['confidence']*100).round(1)
            show=show.rename(columns={'horizon':'Horizon','samples':'Samples','p_up':'UP %','p_sideways':'Sideways %','p_down':'DOWN %','mean_return':'Mean return %','confidence':'Confidence %','data_status':'Data','time_bucket':'Time window','window_mode':'Matching','entry_status':'Entry window','edge':'Edge'})
            show['Edge']=(show['Edge']*100).round(1)
            st.caption('V1.8.28 mempertahankan Time Intelligence dan Dynamic Opportunity Engine serta menambahkan Deep Analysis Monitoring Hub. ACTIVE/DEVELOPING adalah kondisi model, bukan sinyal beli otomatis. Probabilitas adalah estimasi historis/model, bukan kepastian arah harga.')
            st.dataframe(show,width='stretch',hide_index=True)
            active=show[show['Entry window'].isin(['ACTIVE','DEVELOPING'])]
            if not active.empty:
                st.info('Entry Window terdeteksi pada horizon: '+', '.join(active['Horizon'].astype(str).tolist())+'. Tetap cek Risk Engine dan invalidation sebelum mengambil keputusan.')
            else:
                st.warning('Belum ada Entry Window yang cukup kuat. Sistem mempertahankan WAIT/NEUTRAL dan tidak memaksa sinyal.')

        st.markdown('### 🕘 AI Intraday Time Map • WIB')
        tm=intraday_time_map(selected_ticker)
        if tm.empty:
            st.info('Belum ada data intraday yang cukup untuk membentuk Time Map saham ini.')
        else:
            tmshow=tm.copy()
            for c in ['up_pct','sideways_pct','down_pct','mean_return_pct','confidence_pct']:
                if c in tmshow: tmshow[c]=pd.to_numeric(tmshow[c],errors='coerce').round(1)
            tmshow=tmshow.rename(columns={'time':'Waktu','session':'Sesi','horizon':'Horizon','samples':'Samples','up_pct':'UP %','sideways_pct':'Sideways %','down_pct':'DOWN %','mean_return_pct':'Mean Return %','confidence_pct':'Confidence %','status':'Status','note':'Catatan'})
            st.dataframe(tmshow,width='stretch',hide_index=True)
            st.caption('Time Map berlaku per ticker. Setiap checkpoint adalah evaluasi kondisi historis/model pada jam yang sama, bukan janji arah harga. Status ACTIVE/DEVELOPING tetap harus dibaca bersama Risk Engine dan invalidation.')

        st.markdown('### 💡 Why this stock?')
        reasons=[]
        if str(rr.trend)=='BULLISH': reasons.append('Harga dan moving-average menunjukkan struktur tren bullish.')
        else: reasons.append('Struktur moving-average belum mendukung tren bullish penuh.')
        if pd.notna(rr.relative_strength): reasons.append(f"Relative strength terhadap IHSG: {float(rr.relative_strength):.1f}/100.")
        reasons.append(f"Kondisi pasar: {rr.market_regime}; sektor proxy: {rr.sector}.")
        reasons.append(f"Risk engine menilai level risiko {rr.risk} dengan R/R target-2 {float(rr.risk_reward_2):.2f} jika level tersedia." if pd.notna(rr.risk_reward_2) else 'Risk/reward target belum tersedia.')
        for x in reasons: st.markdown(f'- {x}')

        st.markdown('### 🧩 AI Decision Breakdown')
        bd1,bd2,bd3,bd4=st.columns(4)
        bd1.metric('Baseline technical', f"{float(rr.baseline_score):.1f}" if 'baseline_score' in rr and pd.notna(rr.baseline_score) else '—')
        bd2.metric('Market score', f"{float(rr.market_score):.1f}" if pd.notna(rr.market_score) else '—')
        bd3.metric('Sector score', f"{float(rr.sector_score):.1f}" if pd.notna(rr.sector_score) else '—')
        bd4.metric('Relative strength', f"{float(rr.relative_strength):.1f}" if pd.notna(rr.relative_strength) else '—')
        rr_rank=rr.get('rank', np.nan)
        rr_pct=rr.get('relative_percentile', np.nan)
        if pd.notna(rr_rank):
            pct=float(rr_pct)*100 if pd.notna(rr_pct) else np.nan
            st.caption(f"Relative universe rank: #{int(rr_rank)} dari {len(universe)} • percentile {pct:.1f}%" if np.isfinite(pct) else f"Relative universe rank: #{int(rr_rank)} dari {len(universe)}")
        st.markdown('### 📐 Price Context')
        pc1,pc2,pc3,pc4=st.columns(4)
        pc1.metric('Vs MA20', f"{float(rr.price_vs_ma20)*100:+.2f}%" if 'price_vs_ma20' in rr and pd.notna(rr.price_vs_ma20) else '—')
        pc2.metric('Vs MA50', f"{float(rr.price_vs_ma50)*100:+.2f}%" if 'price_vs_ma50' in rr and pd.notna(rr.price_vs_ma50) else '—')
        pc3.metric('20D high gap', f"{float(rr.high20_gap)*100:+.2f}%" if 'high20_gap' in rr and pd.notna(rr.high20_gap) else '—')
        pc4.metric('20D low gap', f"{float(rr.low20_gap)*100:+.2f}%" if 'low20_gap' in rr and pd.notna(rr.low20_gap) else '—')

    st.markdown('### 📊 Technical Snapshot')
    tech=pd.DataFrame([{
        'MA20':float(r.ma20) if pd.notna(r.ma20) else np.nan,
        'MA50':float(r.ma50) if pd.notna(r.ma50) else np.nan,
        'RSI14':float(r.rsi14) if pd.notna(r.rsi14) else np.nan,
        'MACD':float(r.macd) if pd.notna(r.macd) else np.nan,
        'ATR14':float(r.atr14) if pd.notna(r.atr14) else np.nan,
        'Vol ratio':float(r.vol_ratio) if pd.notna(r.vol_ratio) else np.nan,
        '20D high gap':float(r.high20_gap)*100 if pd.notna(r.high20_gap) else np.nan,
        '20D low gap':float(r.low20_gap)*100 if pd.notna(r.low20_gap) else np.nan,
    }])
    st.dataframe(tech.round(3),width='stretch',hide_index=True)
    st.caption('Fundamental, berita/sentimen, dan market-cap akan ditambahkan setelah sumber data fundamental/news resmi masuk. Modul ini sengaja tidak mengarang angka.')


def render_history_shadow():
    """V1.8.40 immutable recommendation history + shadow portfolio dashboard."""
    recommendation_history.ensure_tables(DB)
    st.subheader('📚 Recommendation History + Shadow Portfolio')
    st.caption('Fondasi evaluasi jujur: rekomendasi full-universe dikunci saat dibuat. Hasil setelahnya tidak boleh mengganti rekomendasi lama. Shadow Portfolio memakai data sesi berikutnya untuk menghindari look-ahead.')

    perf = recommendation_history.performance_summary(DB)
    m1,m2,m3,m4,m5,m6 = st.columns(6)
    m1.metric('Paper positions', perf.get('positions',0))
    m2.metric('Aktif / menunggu', perf.get('open',0))
    m3.metric('Closed valid', perf.get('closed',0))
    wr=perf.get('win_rate')
    m4.metric('Win rate', f'{wr:.1f}%' if wr is not None else '—')
    ar=perf.get('avg_return')
    m5.metric('Avg return', f'{ar:+.2f}%' if ar is not None else '—')
    dd=perf.get('max_drawdown')
    m6.metric('Max drawdown', f'{dd:.2f}%' if dd is not None else '—')

    p1,p2,p3,p4=st.columns(4)
    aw=perf.get('avg_win'); al=perf.get('avg_loss'); pf=perf.get('profit_factor')
    p1.metric('Wins', perf.get('wins',0))
    p2.metric('Losses', perf.get('losses',0))
    p3.metric('Avg win / loss', (f'{aw:+.2f}% / {al:+.2f}%' if aw is not None and al is not None else '—'))
    p4.metric('Profit factor', ('∞' if pf == float('inf') else (f'{pf:.2f}' if pf is not None else '—')))

    st.info('Aturan fondasi V1.8.40: snapshot hanya dikunci dari phase **full-universe**. Entry paper trade baru dievaluasi mulai **sesi berikutnya**. Jika satu candle harian menyentuh stop dan target sekaligus, hasil diberi status **AMBIGUOUS** dan tidak dipaksa menjadi menang/kalah.')

    shadow = recommendation_history.read_shadow(DB, 300)
    st.markdown('### 🧪 Shadow Portfolio')
    if shadow.empty:
        st.info('Belum ada paper position. Setelah worker menyelesaikan full-universe cycle berikutnya, kandidat Top Opportunity akan mulai dicatat otomatis.')
    else:
        show=shadow.copy()
        for c in ['entry_price','planned_stop','planned_target_1','planned_target_2','last_price','exit_price','return_pct','max_favorable_pct','max_adverse_pct']:
            if c in show: show[c]=pd.to_numeric(show[c],errors='coerce').round(2)
        show=show.rename(columns={
            'ticker':'ticker','company':'Company','recommended_at':'Recommended At','rank':'Rank','status':'Status',
            'entry_low':'Entry Low','entry_high':'Entry High','entry_at':'Entry At','entry_price':'Entry Price',
            'planned_stop':'Stop','planned_target_1':'Target 1','planned_target_2':'Target 2','last_price':'Last Price',
            'closed_at':'Closed At','exit_price':'Exit Price','return_pct':'Return %','exit_reason':'Exit Reason',
            'max_favorable_pct':'MFE %','max_adverse_pct':'MAE %'
        })
        cols=[c for c in ['ticker','Company','Rank','Status','Recommended At','Entry Low','Entry High','Entry At','Entry Price','Stop','Target 1','Target 2','Last Price','Closed At','Exit Price','Return %','MFE %','MAE %','Exit Reason'] if c in show.columns]
        render_clickable_dataframe(show,cols,2)

    hist = recommendation_history.read_history(DB, 500)
    st.markdown('### 🔒 Locked Recommendation History')
    if hist.empty:
        st.info('Belum ada locked recommendation snapshot.')
    else:
        hs=hist.copy()
        for c in ['opportunity_score','ai_score','p_up','p_down','confidence','reference_price','entry_low','entry_high','stop_loss','target_1','target_2','risk_reward','outcome_return_pct']:
            if c in hs: hs[c]=pd.to_numeric(hs[c],errors='coerce').round(3)
        for c in ['p_up','p_down','confidence']:
            if c in hs: hs[c]=(pd.to_numeric(hs[c],errors='coerce')*100).round(1)
        hs=hs.rename(columns={
            'generated_at':'Generated','market_date':'Market Date','rank':'Rank','recommendation':'Recommendation',
            'opportunity_state':'State','entry_status':'Entry Window','opportunity_score':'Opportunity Score','ai_score':'AI Score',
            'p_up':'P(UP) %','p_down':'P(DOWN) %','confidence':'Confidence %','reference_price':'Reference Price',
            'entry_low':'Entry Low','entry_high':'Entry High','stop_loss':'Stop','target_1':'Target 1','target_2':'Target 2',
            'risk_reward':'R/R','risk':'Risk','market_regime':'IHSG Regime','outcome_status':'Outcome','outcome_return_pct':'Outcome %','outcome_at':'Outcome At'
        })
        cols=[c for c in ['Generated','Market Date','Rank','ticker','company','Recommendation','State','Entry Window','Opportunity Score','AI Score','P(UP) %','Confidence %','Reference Price','Entry Low','Entry High','Stop','Target 1','Target 2','R/R','Risk','IHSG Regime','Outcome','Outcome %','Outcome At'] if c in hs.columns]
        render_clickable_dataframe(hs,cols,2)

    st.caption(f"History last update: {state_get('recommendation_history_at') or 'belum ada'} • Shadow last update: {state_get('shadow_portfolio_at') or 'belum ada'}")

def render_alert_simulator():
    """V1.8.34 simulator for alert rules + WhatsApp Cloud API delivery."""
    st.markdown('#### 🧪 Notification Simulator')
    st.caption('Menguji Smart Alert + Notification Queue memakai data sintetis. Tidak mengirim WhatsApp dan tidak mengubah snapshot real.')

    c1, c2 = st.columns(2)
    with c1:
        if st.button('▶ Jalankan Alert + Queue Simulator', key='run_alert_queue_simulator'):
            rows = notification_engine.run_queue_simulator()
            df = pd.DataFrame(rows)
            st.dataframe(df, width='stretch', hide_index=True)
            passed = int((df['Result'] == 'PASS').sum()) if not df.empty else 0
            total = len(df)
            if total and passed == total:
                st.success(f'✅ Notification pipeline PASS {passed}/{total}. Alert digabung per ticker, perubahan kecil ditahan, dan Top-5 + Outside Top-5 tetap terdeteksi.')
            else:
                st.error(f'❌ Pipeline hanya PASS {passed}/{total}. Perlu pemeriksaan sebelum WhatsApp diaktifkan.')
    with c2:
        st.markdown('**WhatsApp status**')
        status = notification_engine.whatsapp_status(CFGD)
        st.metric('Mode', status['mode'])
        st.caption(status['note'])
        st.caption(f"Credentials: {'READY' if status['credentials_present'] else 'NOT SET'} • Send flag: {'ON' if status['send_enabled'] else 'OFF'}")
        if st.button('🧪 Test WhatsApp Adapter (offline)', key='test_whatsapp_adapter'):
            test = notification_engine.run_whatsapp_adapter_test(CFGD)
            st.success('✅ Adapter offline test PASS. Tidak ada pesan WhatsApp yang dikirim.')
            st.code(test['message_preview'])
        if status.get('mode') == 'WHATSAPP_SEND_READY':
            st.warning('⚠️ WhatsApp LIVE siap. Tombol berikut akan benar-benar mengirim 1 pesan uji ke WHATSAPP_TO.')
            confirm = st.checkbox('Saya sadar ini mengirim pesan WhatsApp sungguhan.', key='confirm_whatsapp_live_test')
            if st.button('📲 Kirim 1 Pesan Uji WhatsApp', key='send_whatsapp_live_test', disabled=not confirm):
                sample = {
                    'ticker':'TEST', 'company':'AI Stock Intelligence', 'severity':'HIGH',
                    'title':'WhatsApp live connection test', 'alert_types':['SYSTEM_TEST'],
                    'source_alert_count':1, 'reasons':['Manual live connectivity test'],
                    'old_value':'OFFLINE', 'new_value':'LIVE',
                }
                result = notification_engine.send_whatsapp_text(notification_engine.render_whatsapp_message(sample), CFGD, dry_run=False)
                if result.get('ok'):
                    st.success(f"✅ WhatsApp live test berhasil. HTTP {result.get('status_code')}")
                else:
                    st.error(f"❌ WhatsApp live test gagal: {result.get('error') or result.get('response')}")

    dstat = notification_engine.delivery_status(DB_DIR)
    st.markdown('**Delivery Queue V1.8.32**')
    dc = dstat.get('counts', {})
    d1,d2,d3,d4,d5 = st.columns(5)
    d1.metric('Queued', dc.get('QUEUED',0))
    d2.metric('Sending', dc.get('SENDING',0))
    d3.metric('Sent', dc.get('SENT',0))
    d4.metric('Failed', dc.get('FAILED',0))
    d5.metric('Dead letter', dc.get('DEAD_LETTER',0))
    st.caption('Worker otomatis mencoba QUEUED/FAILED saat WhatsApp benar-benar di-enable. Saat masih QUEUE_ONLY, antrean tetap aman dan tidak dianggap gagal.')

    st.caption('Pipeline: Smart Alert → Priority Aggregation → Dedup/Cooldown → Persistent Outbox → Delivery Engine → WhatsApp Cloud API. Pengiriman nyata tetap disabled sampai enablement + credentials lengkap.')
    outbox_path = os.path.join(DB_DIR, 'latest_notifications.json')
    if os.path.exists(outbox_path):
        try:
            payload = json.load(open(outbox_path, 'r', encoding='utf-8'))
            st.markdown('##### 📬 Latest Notification Queue')
            st.write(f"Queued: **{int(payload.get('count', 0))}** • Suppressed: **{int(payload.get('suppressed_count', 0))}**")
            notifications = payload.get('notifications') or []
            for n in notifications[:5]:
                sev = str(n.get('severity', 'MEDIUM'))
                msg = n.get('whatsapp_message', '')
                if sev == 'HIGH':
                    st.error(msg)
                elif sev == 'MEDIUM':
                    st.warning(msg)
                else:
                    st.info(msg)
        except Exception:
            pass

    st.caption('Aturan inti: Top-5 bersifat dinamis; saham di luar Top-5 tetap dapat memicu alert jika Opportunity Score menembus ambang kuat atau Entry Window menjadi ACTIVE dengan kondisi memadai. V1.8.34 menggabungkan beberapa event satu ticker menjadi satu pesan dan mempertahankan status delivery di outbox.')

def render_app():
    st.set_page_config(page_title='AI Stock Intelligence V1.8.34', page_icon='📈', layout='wide')
    st.markdown("""
    <style>
      .block-container{padding-top:1.15rem;padding-bottom:2rem;max-width:1500px}
      h1{font-size:clamp(1.55rem,3vw,2.35rem)!important;margin-bottom:.15rem!important}
      h2,h3{letter-spacing:-.02em}
      div[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.22);border-radius:14px;padding:12px 14px;background:rgba(128,128,128,.045)}
      div[data-testid="stMetricLabel"]{font-weight:650}
      .asi-card{border:1px solid rgba(128,128,128,.22);border-radius:16px;padding:14px 16px;margin:.35rem 0 .8rem 0;background:rgba(128,128,128,.04)}
      .asi-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
      .asi-pill{display:inline-block;padding:5px 10px;border-radius:999px;font-weight:750;font-size:.82rem;border:1px solid transparent}
      .asi-green{color:#16a34a;background:rgba(22,163,74,.12);border-color:rgba(22,163,74,.25)}
      .asi-red{color:#dc2626;background:rgba(220,38,38,.12);border-color:rgba(220,38,38,.25)}
      .asi-yellow{color:#d97706;background:rgba(217,119,6,.13);border-color:rgba(217,119,6,.27)}
      .asi-gray{color:#6b7280;background:rgba(107,114,128,.12);border-color:rgba(107,114,128,.22)}
      @media(max-width:700px){.block-container{padding-left:.75rem;padding-right:.75rem}.asi-card{padding:11px 12px}div[data-testid="stMetric"]{padding:9px 10px}}
    </style>
    """, unsafe_allow_html=True)

    universe, source = load_universe()
    res = read_opportunities(100)
    model = load_active_model()
    engine_last = state_get('engine_last_run')
    trained = state_get('active_model_trained_at') or state_get('jst_trained_at')
    wf = state_get('walk_forward_accuracy')
    wf_at = state_get('walk_forward_at')
    active_name = state_get('active_model_name') or ('JST' if model else 'BACKGROUND')
    snap_at, snap_source = _snapshot_status()
    outlook = read_outlook()
    ihsg = ihsg_snapshot()
    ready = bool(snap_at and not res.empty)

    def _pill(text, kind='gray'):
        safe = str(text).replace('<','&lt;').replace('>','&gt;')
        return f'<span class="asi-pill asi-{kind}">{safe}</span>'

    def _kind(value):
        x = str(value or '').upper()
        if any(k in x for k in ['READY','BUY','UP','ACTIVE','BULL','GOOD','PASS','LOW']): return 'green'
        if any(k in x for k in ['SELL','AVOID','DOWN','ERROR','FAILED','BEAR','HIGH']): return 'red'
        if any(k in x for k in ['WATCH','WAIT','NEAR','DEVELOP','SIDEWAYS','WARNING','MEDIUM','PREPAR']): return 'yellow'
        return 'gray'

    st.title('📈 AI Stock Intelligence V1.8.34')
    st.caption('Continuous Intelligence • Rank → Opportunity → Alert → Deep Analysis → Risk → Hold → Overnight')
    st.markdown('<div class="asi-row">'+_pill('READY' if ready else 'PREPARING', 'green' if ready else 'yellow')+_pill(f'Model {active_name}', 'green' if model else 'gray')+_pill(f'Snapshot {snap_at or "belum ada"}', 'green' if snap_at else 'gray')+'</div>', unsafe_allow_html=True)

    forced_detail=str(st.query_params.get('ticker','')).upper().replace('.JK','').strip() or None
    if forced_detail:
        b1,b2=st.columns([1,5])
        if b1.button('← Dashboard',key='back_dashboard',use_container_width=True):
            st.query_params.clear()
            st.rerun()
        b2.caption(f'Deep Analysis aktif untuk {forced_detail}. Tombol ← Dashboard kembali ke halaman utama.')
        stock_search_panel(universe,res,forced_ticker=forced_detail)
        return

    tab_home, tab_opp, tab_search, tab_history, tab_ai, tab_engine = st.tabs(['🏠 Ringkasan','🚀 Opportunity','🔎 Analisis Saham','📚 History & Shadow','🧠 AI & Validasi','⚙️ Engine / Log'])

    with tab_home:
        st.subheader('Ringkasan Sistem')
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric('Universe', len(universe))
        c2.metric('Intelligence', 'READY' if ready else 'PREPARING')
        c3.metric('Snapshot', snap_at or 'Belum ada')
        c4.metric('Active model', active_name if model else 'BACKGROUND')
        c5.metric('WF accuracy', f'{float(wf)*100:.1f}%' if wf else '—')

        st.subheader('🇮🇩 IHSG • Market Context')
        if ihsg:
            trend=str(ihsg.get('trend','N/A'))
            gap=ihsg.get('ma_gap_pct',np.nan)
            market_note = ('Pasar mendukung posisi agresif secara relatif, tetapi sinyal saham tetap wajib lolos filter AI + risiko.' if trend=='BULLISH' else
                           'Pasar netral. Prioritaskan saham dengan relative strength dan entry window yang lebih kuat.' if trend=='SIDEWAYS' else
                           'Pasar sedang defensif. Kandidat Top 5 tetap ditampilkan, tetapi konteks risiko IHSG perlu diberi bobot lebih besar.' if trend=='BEARISH' else
                           'Data IHSG belum cukup untuk menentukan regime.')
            i1,i2,i3,i4,i5=st.columns(5)
            i1.metric('IHSG',f"{ihsg['close']:,.2f}")
            i2.metric('Perubahan 1D',f"{ihsg['change_pct']:+.2f}%" if np.isfinite(ihsg['change_pct']) else '—')
            i3.metric('MA20',f"{ihsg['ma20']:,.2f}" if np.isfinite(ihsg['ma20']) else '—')
            i4.metric('Jarak vs MA20',f"{gap:+.2f}%" if np.isfinite(gap) else '—')
            i5.metric('Market Regime',trend)
            st.markdown('<div class="asi-card"><div class="asi-row">'+_pill(trend,_kind(trend))+'</div><div style="margin-top:8px">'+market_note+'</div></div>',unsafe_allow_html=True)
            st.caption('IHSG juga sudah menjadi bagian dari konteks model/opportunity melalui fitur market regime, relative strength, market score, dan alignment yang tersedia pada engine.')
        else:
            st.warning('Data IHSG belum tersedia pada snapshot ini. Ranking saham tetap berjalan, tetapi market context belum dapat ditampilkan.')

        dyn = read_dynamic_opportunities(int(CFGD.get('dynamic_opportunity_count',5)))
        st.subheader('🚀 Top Opportunity Saat Ini')
        if dyn.empty:
            st.info('Belum ada kandidat yang memenuhi filter relatif + AI + risiko.')
        else:
            dshow=dyn.copy()
            for c in ['opportunity_score','ai_score']:
                if c in dshow: dshow[c]=pd.to_numeric(dshow[c],errors='coerce').round(1)
            for c in ['p_up','p_down','confidence','relative_percentile','time_edge','time_confidence']:
                if c in dshow: dshow[c]=(pd.to_numeric(dshow[c],errors='coerce')*100).round(1)
            dshow=dshow.rename(columns={'opportunity_score':'Opportunity Score','state':'State','entry_status':'Entry Window','close':'Price','entry_low':'Entry Low','entry_high':'Entry High','stop_loss':'Stop Loss','target_1':'Target 1','target_2':'Target 2','risk':'Risk','ai_score':'AI Score','p_up':'P(UP) %','p_down':'P(DOWN) %','confidence':'AI Conf. %','market_regime':'IHSG Regime'})
            cols=[c for c in ['rank','ticker','company','Opportunity Score','State','Entry Window','Price','Entry Low','Entry High','Stop Loss','Target 1','Target 2','Risk','AI Score','P(UP) %','P(DOWN) %','AI Conf. %','IHSG Regime'] if c in dshow.columns]
            render_clickable_dataframe(dshow.head(5), cols)
            top=dyn.iloc[0]
            st.markdown('<div class="asi-card"><b>#1 '+str(top.ticker)+'</b><br><div class="asi-row">'+_pill(getattr(top,'state','—'),_kind(getattr(top,'state','—')))+_pill('Entry '+str(getattr(top,'entry_status','—')),_kind(getattr(top,'entry_status','—')))+_pill('Risk '+str(getattr(top,'risk','—')),_kind(getattr(top,'risk','—')))+'</div></div>',unsafe_allow_html=True)

        st.subheader('🧭 Next-Session Intelligence')
        if outlook:
            o1,o2,o3,o4,o5=st.columns(5)
            o1.metric('Next session',outlook['next_session']); o2.metric('🟢 BUY',outlook['buy_count']); o3.metric('🔴 SELL/AVOID',outlook['sell_count']); o4.metric('🟡 NEAR ENTRY',outlook['near_count']); o5.metric('⚪ WAIT',outlook['wait_count'])
            st.caption(f"Snapshot {outlook['market_status']} • generated {outlook['generated_at']}")
        else:
            st.info('AI sedang menyiapkan snapshot sesi berikutnya.')

    with tab_opp:
        st.subheader('🚀 Dynamic Opportunity Engine')
        dyn=read_dynamic_opportunities(int(CFGD.get('dynamic_opportunity_count',5)))
        if dyn.empty:
            st.info('Belum ada kandidat Dynamic Opportunity.')
        else:
            dshow=dyn.copy()
            for c in ['opportunity_score','ai_score']:
                if c in dshow: dshow[c]=pd.to_numeric(dshow[c],errors='coerce').round(1)
            for c in ['p_up','p_down','confidence','relative_percentile','time_edge','time_confidence']:
                if c in dshow: dshow[c]=(pd.to_numeric(dshow[c],errors='coerce')*100).round(1)
            dshow=dshow.rename(columns={'opportunity_score':'Opportunity Score','market_regime':'IHSG Regime','state':'State','entry_status':'Entry Window','horizon':'Horizon','close':'Price','entry_low':'Entry Low','entry_high':'Entry High','stop_loss':'Stop Loss','target_1':'Target 1','target_2':'Target 2','risk_reward':'R/R','risk':'Risk','ai_score':'AI Score','p_up':'P(UP) %','p_down':'P(DOWN) %','confidence':'AI Conf. %','relative_percentile':'Relative %','time_edge':'Time Edge %','time_confidence':'Time Conf. %'})
            cols=[c for c in ['rank','ticker','company','Opportunity Score','State','Entry Window','Horizon','IHSG Regime','Price','Entry Low','Entry High','Stop Loss','Target 1','Target 2','R/R','Risk','AI Score','P(UP) %','P(DOWN) %','AI Conf. %','Relative %','Time Edge %','Time Conf. %'] if c in dshow.columns]
            render_clickable_dataframe(dshow,cols)
            st.caption('Top-5 bersifat dinamis. Kandidat dapat masuk atau keluar ketika kondisi berubah. Output probabilistik, bukan jaminan hasil.')

        st.subheader('🔥 Adaptive AI Opportunities')
        if res.empty:
            st.warning('Belum ada snapshot yang tersimpan.')
        else:
            maxn=int(CFGD.get('max_opportunities',10))
            groups=[('🟢 BUY SETUPS','BUY',maxn),('🟡 NEAR ENTRY','NEAR ENTRY',maxn),('👀 WATCH','WATCH',15),('🔴 SELL / AVOID','SELL/AVOID',maxn),('⚪ WAIT','WAIT',20)]
            for title,decision,n in groups:
                part=res[res.decision==decision].head(n)
                with st.expander(f'{title} • {len(part)}', expanded=(decision in ['BUY','NEAR ENTRY'])):
                    if part.empty: st.info('Tidak ada kandidat pada status ini.')
                    else:
                        cols=[c for c in ['rank','ticker','company','close','ai_score','jst_p_up','jst_p_down','jst_confidence','market_regime','trend','risk','entry_low','entry_high','stop_loss','target_1','target_2','risk_reward_2','decision_score'] if c in part.columns]
                        render_clickable_dataframe(part,cols,3)

        st.subheader('🕒 Hold Horizon + Overnight Intelligence')
        hold=read_hold_intelligence(int(CFGD.get('hold_candidate_pool',20)))
        if hold.empty:
            st.info('Hold Intelligence belum memiliki kandidat.')
        else:
            hshow=hold.copy()
            for c in ['p_hold','p_up','p_down','confidence','time_edge']:
                if c in hshow: hshow[c]=(pd.to_numeric(hshow[c],errors='coerce')*100).round(1)
            hshow=hshow.rename(columns={'hold_score':'Hold Score','state':'State','hold_horizon':'Hold Horizon','p_hold':'P(Hold) %','p_up':'P(UP) %','p_down':'P(DOWN) %','confidence':'Confidence %','overnight_state':'Overnight','next_session_bias':'Next Session','current_price':'Price','stop_loss':'Stop Loss','target_1':'Target 1','risk':'Risk','market_regime':'IHSG Regime'})
            cols=[c for c in ['opportunity_rank','ticker','company','Hold Score','State','Hold Horizon','Overnight','Next Session','IHSG Regime','P(Hold) %','P(UP) %','P(DOWN) %','Confidence %','Price','Stop Loss','Target 1','Risk'] if c in hshow.columns]
            render_clickable_dataframe(hshow,cols,2)

    with tab_search:
        forced_ticker=str(st.query_params.get('ticker','')).upper().replace('.JK','').strip() or None
        stock_search_panel(universe,res,forced_ticker=forced_ticker)

    with tab_history:
        render_history_shadow()

    with tab_ai:
        st.subheader('🧠 AI Intelligence Detail')
        detail=intelligence_detail(res)
        if detail.empty:
            st.info('Detail AI belum tersedia.')
        else:
            top_detail=detail.head(25).copy(); render_clickable_dataframe(top_detail,top_detail.columns.tolist(),3)

        st.subheader('🧪 Model Validation • Out-of-Sample')
        vsummary,vdetail=read_model_validation()
        if wf:
            w1,w2,w3=st.columns(3)
            w1.metric('Walk-forward','SELESAI'); w2.metric('WF accuracy',f'{float(wf)*100:.1f}%'); w3.metric('Last validation',wf_at or '—')
            st.success('Walk-forward validation sudah selesai. Nilai di atas berasal dari evaluasi out-of-sample yang tersimpan oleh engine.')
        else:
            st.warning('Walk-forward belum memiliki hasil tersimpan.')
        if not vsummary.empty:
            primary=int(CFGD.get('primary_target_horizon',3)); primary_df=vsummary[vsummary.horizon_days==primary] if 'horizon_days' in vsummary.columns else vsummary; best=primary_df.iloc[0] if not primary_df.empty else vsummary.iloc[0]
            vc1,vc2,vc3=st.columns(3); vc1.metric('Primary horizon',f'{primary}D'); vc2.metric('Best model',str(best['model'])); vc3.metric('Balanced accuracy',f"{best['balanced_accuracy_mean']*100:.1f}%")
            show=vsummary.copy()
            for c in ['accuracy_mean','accuracy_std','balanced_accuracy_mean','macro_f1_mean','up_precision_mean','up_recall_mean','up_f1_mean']:
                if c in show: show[c]=(show[c]*100).round(1)
            st.dataframe(show,width='stretch',hide_index=True)
            with st.expander('Detail fold model validation'):
                st.dataframe(vdetail.round(4),width='stretch',hide_index=True)
        elif wf:
            st.info('Walk-forward sudah selesai, tetapi laporan perbandingan model yang lebih rinci (model_validation.json) belum tersedia. Ini berbeda dari status walk-forward di atas.')
        else:
            st.info('Laporan validasi model rinci belum tersedia.')

        with st.expander('🧪 Data Quality Audit'):
            audit=data_quality_audit(universe,res); collected=int(state_get('last_collection_ok') or 0); collection_errors=int(state_get('last_collection_errors') or 0); coverage=float(state_get('last_collection_coverage_pct') or (100*collected/len(universe) if len(universe) else 0))
            a1,a2,a3,a4=st.columns(4); a1.metric('Collected',f'{collected}/{len(universe)}'); a2.metric('Errors',collection_errors); a3.metric('Analyzed',len(res)); a4.metric('Coverage',f'{coverage:.1f}%')
            if not audit.empty: render_smart_dataframe(audit.head(100),round_digits=3)
            else: st.success('Semua saham dalam snapshot memiliki data yang cukup.')

        with st.expander('🛡️ Risk Engine Audit'):
            ra=risk_engine_audit(res)
            if ra:
                r1,r2,r3,r4=st.columns(4); r1.metric('Risk levels',f"{ra['risk_levels']}/{ra['analyzed']}"); r2.metric('Entry levels',f"{ra['entry_levels']}/{ra['analyzed']}"); r3.metric('Stop loss',f"{ra['stop_loss']}/{ra['analyzed']}"); r4.metric('R/R qualified',f"{ra['rr_qualified']}/{ra['analyzed']}")

    with tab_engine:
        st.subheader('⚙️ Background Engine')
        e1,e2,e3,e4=st.columns(4)
        e1.metric('Engine','READY' if ready else 'PREPARING'); e2.metric('Last cycle',engine_last or 'Belum ada'); e3.metric('Last JST training',trained or 'Belum ada'); e4.metric('Last walk-forward',wf_at or 'Belum ada')
        st.caption(f"Dynamic Opportunity: {state_get('dynamic_opportunity_at') or 'belum berjalan'} • phase={state_get('dynamic_opportunity_phase') or '—'} • candidates={state_get('dynamic_opportunity_count') or 0}")
        st.info('Engine memakai scheduler otomatis: snapshot awal cepat, full refresh, retraining, walk-forward, lalu pembaruan opportunity. Tidak ada tombol manual yang wajib ditekan.')

        with st.expander('📋 Log proses AI',expanded=True):
            logdf=read_engine_log(40)
            if logdf.empty: st.write('Belum ada log.')
            else: st.dataframe(logdf,width='stretch',hide_index=True)

        with st.expander('🧭 Universe & Ticker Intelligence'):
            regsum=registry_summary(); rc1,rc2,rc3,rc4=st.columns(4); rc1.metric('Registry',regsum['total']); rc2.metric('Direct',regsum['direct']); rc3.metric('Resolved',regsum['resolved']); rc4.metric('Unresolved',regsum['unresolved'])
            registry=read_universe_registry(250)
            if not registry.empty:
                show_cols=[c for c in ['ticker','company','provider_symbol','status','confidence','source','last_checked','note'] if c in registry.columns]
                render_smart_dataframe(registry[show_cols],round_digits=3)

        with st.expander('🔔 Notification & WhatsApp Simulator'):
            render_alert_simulator()

        with st.expander('📊 Semua Ranking AI'):
            if res.empty: st.info('Belum ada ranking tersimpan.')
            else: render_smart_dataframe(res,round_digits=3)

if __name__ == '__main__':
    render_app()
