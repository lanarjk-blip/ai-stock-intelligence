"""Hard live Time Map analysis for Streamlit/APK.
R86 patches st.dataframe directly so Time Map cannot silently render stale-only history.
Previous-session data is never accepted as current-market-day live evidence.
"""
from __future__ import annotations
import json, math, os, re
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests

_INSTALLED=False
_BASE=os.path.dirname(os.path.abspath(__file__))
_TICKER_RE=re.compile(r"^[A-Z0-9]{2,6}$")

def _cfg():
    try:
        with open(os.path.join(_BASE,"config.json"),"r",encoding="utf-8") as f:return json.load(f)
    except Exception:return {}

def _num(v,default=None):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except Exception:return default

def _ticker(v):
    s=str(v or "").upper().replace(".JK","").strip().split("•",1)[0].strip()
    t=re.split(r"[^A-Z0-9]+",s,maxsplit=1)[0].strip()
    return t if _TICKER_RE.fullmatch(t or "") else ""

def _active_ticker(st):
    try:
        v=st.query_params.get("ticker")
        if isinstance(v,(list,tuple)):v=v[0] if v else ""
        t=_ticker(v)
        if t:return t
    except Exception:pass
    for k in ("stock_search_selection","stock_search_query","selected_ticker","active_ticker","ticker"):
        try:t=_ticker(st.session_state.get(k))
        except Exception:t=""
        if t:return t
    return ""

def _is_map(df):
    return isinstance(df,pd.DataFrame) and not df.empty and {"UP %","DOWN %","Samples"}.issubset(set(map(str,df.columns)))

def _time_col(df):
    for c in df.columns:
        n=str(c).lower().strip()
        if n in {"waktu","time","jam","checkpoint","slot"} or "waktu" in n or "time" in n:return c
    return None

def _slot(v):
    m=re.search(r"(\d{1,2}):(\d{2})",str(v or "")); return int(m.group(1))*60+int(m.group(2)) if m else None

def _row(df):
    tc=_time_col(df)
    if tc is None:return df.index[0]
    now=datetime.now(ZoneInfo("Asia/Jakarta")); target=now.hour*60+(0 if now.minute<30 else 30)
    best=None
    for idx,v in df[tc].items():
        minute=_slot(v)
        if minute is None:continue
        gap=target-minute
        if gap==0:return idx
        if gap>=0 and (best is None or gap<best[0]):best=(gap,idx)
    return best[1] if best else df.index[0]

def _session():
    try:
        import market_session
        r=dict(market_session.session_status(_cfg()) or {})
        return str(r.get("status") or "UNKNOWN").upper(),bool(r.get("live")),str(r.get("market_date") or ""),str(r.get("wib") or "")
    except Exception:
        now=datetime.now(ZoneInfo("Asia/Jakarta")); return "UNKNOWN",False,now.date().isoformat(),now.isoformat()

def _provider_frame(ticker):
    cfg=_cfg()
    try:
        import app
        value=app.fetch_chart_recover(ticker,max(2,int(cfg.get("intraday_refresh_period_days",5))),cfg.get("intraday_interval","30m"))
        if isinstance(value,tuple):
            for item in value:
                if isinstance(item,pd.DataFrame):return item
        if isinstance(value,pd.DataFrame):return value
    except Exception:pass
    params={"range":"5d","interval":str(cfg.get("intraday_interval") or "30m"),"events":"history"}
    headers={"User-Agent":str(cfg.get("user_agent") or "AI-Stock-Intelligence/1.8")}; timeout=int(cfg.get("request_timeout_seconds") or 8)
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        try:
            r=requests.get(f"https://{host}/v8/finance/chart/{ticker}.JK",params=params,timeout=timeout,headers=headers); r.raise_for_status()
            res=((r.json().get("chart") or {}).get("result") or [])
            if not res:continue
            j=res[0]; ts=j.get("timestamp") or []; q=((j.get("indicators") or {}).get("quote") or [{}])[0]
            if ts:return pd.DataFrame({"ts":pd.to_datetime(ts,unit="s",utc=True,errors="coerce"),"open":q.get("open"),"close":q.get("close")})
        except Exception:continue
    return pd.DataFrame()

def _live(ticker):
    cfg=_cfg(); status,is_live,market_date,wib=_session()
    out={
        "ticker":ticker,"market_date":market_date,"market_status":status,
        "live_required":is_live,"fresh":False,"bars":0,"price":None,
        "return_pct":None,"momentum_pct":None,"age_min":None,
        "direction":"WAIT","reason":"","source":"UI_DIRECT"
    }
    try:
        d=_provider_frame(ticker)
        if d.empty or "ts" not in d.columns or "close" not in d.columns:out["reason"]="NO INTRADAY BARS"; return out
        d=d.copy(); d["ts"]=pd.to_datetime(d["ts"],utc=True,errors="coerce"); d=d.dropna(subset=["ts","close"]).sort_values("ts")
        d["wib"]=d["ts"].dt.tz_convert("Asia/Jakarta")
        # Hard boundary. Compatibility contract also retained as: d["wib"].dt.date.astype(str)==market_date
        d = d[d["wib"].dt.date.astype(str) == market_date]
        if d.empty:out["reason"]="NO BAR FOR ACTIVE MARKET DAY"; return out
        first,last=d.iloc[0],d.iloc[-1]
        now=pd.Timestamp(wib); now=now.tz_localize("Asia/Jakarta") if now.tzinfo is None else now.tz_convert("Asia/Jakarta")
        age=max(0.0,(now.tz_convert("UTC")-last["ts"]).total_seconds()/60.0)
        op=_num(first.get("open"),_num(first.get("close"))); cur=_num(last.get("close")); ret=((cur/op)-1)*100 if cur and op else None
        prev=_num(d.iloc[-2].get("close")) if len(d)>=2 else cur; mom=((cur/prev)-1)*100 if cur and prev else 0.0
        direction="WAIT" if ret is None else ("UP" if ret>0.20 and mom>=0 else ("DOWN" if ret<-0.20 and mom<=0 else "SIDEWAYS"))
        stale=max(10,int(cfg.get("intraday_live_stale_minutes") or 25)); session_pause=status in {"BREAK","AFTER_MARKET"}
        fresh=bool(age<=stale or session_pause)
        reason="SESSION PAUSE" if session_pause else ("FRESH" if age<=stale else "DATA LIVE BELUM SEGAR")
        out.update({"fresh":fresh,"bars":int(len(d)),"price":cur,"return_pct":ret,"momentum_pct":mom,"age_min":age,"direction":direction,"reason":reason})
    except Exception as exc:out["reason"]=type(exc).__name__.upper()
    return out

def _hist(row):
    up,down,side=_num(row.get("UP %"),0),_num(row.get("DOWN %"),0),_num(row.get("Sideways %"),0)
    return "SIDEWAYS" if side>=max(up,down) else ("UP" if up>=down else "DOWN")

def _order(df):
    pref=["Waktu","Sesi","Horizon","Live Ticker","Market Date","Market Status","Live Source","Live Price","Live Return %","Live Momentum %","Live Direction","Live Confirmation","Live Match","Match %","Live Age m","Live Bars","Live Reason","Samples","UP %","Sideways %","DOWN %","Mean Return %","Confidence %","Status","Catatan"]
    cols=[c for c in pref if c in df.columns]+[c for c in df.columns if c not in pref]; return df.loc[:,cols]

def _augment(df,st):
    if not _is_map(df):return df
    out=df.copy(); defaults={"Live Ticker":"","Market Date":"","Market Status":"","Live Source":"","Live Price":None,"Live Return %":None,"Live Momentum %":None,"Live Direction":"WAIT","Live Confirmation":"WAIT","Live Match":"WAIT","Match %":None,"Live Age m":None,"Live Bars":0,"Live Reason":""}
    for c,v in defaults.items():
        if c not in out.columns:out[c]=v
    matched=_row(out); ticker=_active_ticker(st)
    if not ticker:
        out.at[matched,"Live Reason"]="TICKER BELUM TERIKAT"; out.at[matched,"Live Match"]="TICKER BELUM TERIKAT"; return _order(out)
    live=_live(ticker)
    mapping={"Live Ticker":"ticker","Market Date":"market_date","Market Status":"market_status","Live Source":"source","Live Price":"price","Live Return %":"return_pct","Live Momentum %":"momentum_pct","Live Direction":"direction","Live Age m":"age_min","Live Bars":"bars","Live Reason":"reason"}
    for col,key in mapping.items():out.at[matched,col]=live[key]
    out.at[matched,"Live Source"]="DIRECT PROVIDER"
    status=live["market_status"]
    if not live["live_required"] and status not in {"BREAK","AFTER_MARKET"}:
        out.at[matched,"Live Confirmation"]="WAIT • MARKET TIDAK LIVE"; out.at[matched,"Live Match"]="MARKET TIDAK LIVE"; return _order(out)
    if not live["fresh"]:
        out.at[matched,"Live Confirmation"]="WAIT • DATA NOT FRESH"; out.at[matched,"Live Match"]="DATA LIVE BELUM SEGAR"; return _order(out)
    h=_hist(out.loc[matched]); ld=live["direction"]
    if ld==h:conf,match="CONFIRMED",100.0
    elif ld=="SIDEWAYS" or h=="SIDEWAYS":conf,match="MIXED • WAIT",50.0
    else:conf,match="DIVERGENCE • WAIT",0.0
    if status=="BREAK":conf=f"{conf} • BREAK"
    elif status not in {"SESSION_1","SESSION_2"}:conf=f"WAIT • {status}"
    out.at[matched,"Live Confirmation"]=conf; out.at[matched,"Live Match"]=conf; out.at[matched,"Match %"]=match
    return _order(out)

def install():
    global _INSTALLED
    if _INSTALLED:return True
    import streamlit as st
    original=st.dataframe
    if getattr(original,"_asi_r86_hard_timemap",False):_INSTALLED=True; return True
    def dataframe(data=None,*args,**kwargs):
        if isinstance(data,pd.DataFrame):data=_augment(data,st)
        return original(data,*args,**kwargs)
    dataframe._asi_r86_hard_timemap=True; st.dataframe=dataframe; _INSTALLED=True; return True
