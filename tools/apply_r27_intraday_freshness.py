from pathlib import Path

# ---------------- config ----------------
p=Path('config.json')
s=p.read_text(encoding='utf-8')
anchor='  "intraday_refresh_minutes": 30,\n'
addition='  "intraday_refresh_minutes": 30,\n  "intraday_refresh_grace_minutes": 15,\n  "intraday_live_price_max_age_minutes": 75,\n'
if addition not in s:
    if anchor not in s: raise SystemExit('intraday_refresh_minutes anchor missing')
    s=s.replace(anchor,addition,1)
p.write_text(s,encoding='utf-8')

# ---------------- app.py ----------------
p=Path('app.py')
s=p.read_text(encoding='utf-8')
if '\nimport market_session\n' not in s:
    anchor='import ai_stock_ask\n'
    if anchor not in s: raise SystemExit('app import anchor missing')
    s=s.replace(anchor,anchor+'import market_session\n',1)

load_block="""def _intraday_load(ticker):
    con=dbcon(); df=pd.read_sql_query('SELECT ts,open,high,low,close,volume FROM intraday WHERE ticker=? ORDER BY ts',con,params=(ticker,)); con.close()
    if not df.empty:
        df['ts']=pd.to_datetime(df.ts,utc=True)
    return df
"""
helpers=r'''

def _utc_timestamp(now=None):
    if now is None:
        return pd.Timestamp.now(tz='UTC')
    ts=pd.Timestamp(now)
    if ts.tzinfo is None:
        return ts.tz_localize('UTC')
    return ts.tz_convert('UTC')


def _intraday_refresh_plan(df, now=None):
    """Return NONE, FULL_HISTORY, or LIGHT_REFRESH based on history and bar age."""
    if df is None or len(df)<60:
        return 'FULL_HISTORY', 'history_lt_60'
    session=market_session.session_status(CFGD,now=now)
    status=str(session.get('status') or '').upper()
    if not (bool(session.get('live')) or status=='BREAK'):
        return 'NONE', f'session_{status or "UNKNOWN"}'
    try:
        latest=pd.to_datetime(df['ts'],utc=True,errors='coerce').dropna().max()
        if pd.isna(latest):
            return 'LIGHT_REFRESH','latest_bar_invalid'
        age_minutes=max(0.0,(_utc_timestamp(now)-latest).total_seconds()/60.0)
    except Exception:
        return 'LIGHT_REFRESH','latest_bar_invalid'
    refresh=float(CFGD.get('intraday_refresh_minutes',30))
    grace=float(CFGD.get('intraday_refresh_grace_minutes',15))
    limit=max(refresh,refresh+grace)
    if age_minutes>limit:
        return 'LIGHT_REFRESH',f'bar_age_{age_minutes:.1f}m'
    return 'NONE',f'bar_age_{age_minutes:.1f}m'


def _intraday_attempt_due(ticker, now=None):
    key='intraday_refresh_attempt_'+str(ticker).upper().replace('.JK','')
    raw=state_get(key)
    if not raw:
        return True,key
    try:
        dt=pd.Timestamp(str(raw))
        if dt.tzinfo is None: dt=dt.tz_localize('UTC')
        else: dt=dt.tz_convert('UTC')
        age=(_utc_timestamp(now)-dt).total_seconds()/60.0
        return age>=float(CFGD.get('intraday_refresh_minutes',30)),key
    except Exception:
        return True,key


def _refresh_intraday_if_needed(ticker, df=None, now=None):
    ticker=str(ticker).upper().replace('.JK','')
    df=_intraday_load(ticker) if df is None else df
    mode,reason=_intraday_refresh_plan(df,now=now)
    if mode=='NONE':
        return df,{'mode':'CACHE','reason':reason,'attempted':False,'updated':False}
    due,key=_intraday_attempt_due(ticker,now=now)
    if not due:
        return df,{'mode':'THROTTLED','reason':reason,'attempted':False,'updated':False}
    stamp=_utc_timestamp(now).isoformat()
    state_set(key,stamp)
    period=(CFGD.get('intraday_period_days',60) if mode=='FULL_HISTORY' else 2)
    try:
        fresh=fetch_chart(ticker,period,CFGD.get('intraday_interval','30m'))
        if fresh is not None and not fresh.empty:
            save_intraday(fresh,ticker)
            df=_intraday_load(ticker)
            state_set('intraday_refresh_success_'+ticker,stamp)
            return df,{'mode':mode,'reason':reason,'attempted':True,'updated':True}
        return df,{'mode':mode,'reason':reason,'attempted':True,'updated':False}
    except Exception as exc:
        state_set('intraday_refresh_error_'+ticker,str(exc)[:300])
        return df,{'mode':mode,'reason':reason,'attempted':True,'updated':False}


def latest_intraday_quote(ticker, now=None):
    """Return a same-market-day price, never an old bar disguised as live."""
    ticker=str(ticker).upper().replace('.JK','')
    df=_intraday_load(ticker)
    if df.empty:
        return None
    session=market_session.session_status(CFGD,now=now)
    status=str(session.get('status') or '').upper()
    if status in {'PRE_MARKET','WEEKEND','HOLIDAY'}:
        return None
    target=str(session.get('market_date') or '')
    d=df.copy()
    d['ts']=pd.to_datetime(d['ts'],utc=True,errors='coerce')
    d=d.dropna(subset=['ts','close'])
    if d.empty:
        return None
    d['wib_date']=d['ts'].dt.tz_convert('Asia/Jakarta').dt.date.astype(str)
    d=d[d['wib_date']==target]
    if d.empty:
        return None
    row=d.iloc[-1]
    bar_ts=pd.Timestamp(row['ts'])
    if bool(session.get('live')) or status=='BREAK':
        age_minutes=max(0.0,(_utc_timestamp(now)-bar_ts).total_seconds()/60.0)
        if age_minutes>float(CFGD.get('intraday_live_price_max_age_minutes',75)):
            return None
    return {
        'price':float(row['close']),
        'ts':bar_ts.isoformat(),
        'source':'INTRADAY_TODAY',
        'market_date':target,
    }
'''
if 'def _intraday_refresh_plan(' not in s:
    if load_block not in s: raise SystemExit('intraday load block missing')
    s=s.replace(load_block,load_block+helpers,1)

old="""def intraday_prediction(ticker):
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
"""
new="""def intraday_prediction(ticker, now=None):
    df=_intraday_load(ticker)
    # History size alone is not freshness. Keep a robust historical sample, then
    # refresh a lightweight recent window when the latest live/break bar is stale.
    df,_refresh_meta=_refresh_intraday_if_needed(ticker,df,now=now)
    if len(df)<60:return pd.DataFrame()
"""
if new not in s:
    if old not in s: raise SystemExit('intraday_prediction legacy block missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# ---------------- dynamic_engine.py ----------------
p=Path('dynamic_engine.py')
s=p.read_text(encoding='utf-8')
old="""def _cached_time_signal(ticker):
    \"\"\"Use the existing V1.8.20.1 time model, but only for a small candidate set.\"\"\"
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
"""
new="""def _cached_time_signal(ticker):
    \"\"\"Time edge plus a same-day intraday quote when one is genuinely fresh.\"\"\"
    base={'edge':0.0,'confidence':0.0,'horizon':'EOD','entry_status':'NEUTRAL',
          'current_price':None,'price_at':'','price_source':'DAILY_FALLBACK'}
    try:
        tp=app.intraday_prediction(str(ticker))
        try:
            quote=app.latest_intraday_quote(str(ticker))
        except Exception:
            quote=None
        if quote:
            base.update({
                'current_price':float(quote.get('price')),
                'price_at':str(quote.get('ts') or ''),
                'price_source':str(quote.get('source') or 'INTRADAY_TODAY'),
            })
        if tp is None or tp.empty:
            return base
        # Prefer EOD for next-session / broad opportunity selection.
        row=tp[tp.horizon.astype(str)=='EOD']
        if row.empty: row=tp.iloc[[0]]
        r=row.iloc[0]
        base.update({'edge':float(r.edge),'confidence':float(r.confidence),
                     'horizon':str(r.horizon),'entry_status':str(r.entry_status)})
        return base
    except Exception:
        return base
"""
if new not in s:
    if old not in s: raise SystemExit('cached time signal block missing')
    s=s.replace(old,new,1)

old="""def _dynamic_entry(row, ts):
    close=float(row.close)
"""
new="""def _dynamic_entry(row, ts):
    live=ts.get('current_price')
    close=float(live) if live is not None and pd.notna(live) else float(row.close)
"""
if new not in s:
    if old not in s: raise SystemExit('dynamic entry anchor missing')
    s=s.replace(old,new,1)

old="""        ts=_cached_time_signal(r.ticker)
        score=_score(r,ts)
        lo,hi=_dynamic_entry(r,ts)
        market_regime,market_score,ihsg_alignment,regime_adjustment=_market_context(r)
"""
new="""        ts=_cached_time_signal(r.ticker)
        score=_score(r,ts)
        lo,hi=_dynamic_entry(r,ts)
        live_price=ts.get('current_price')
        market_price=(float(live_price) if live_price is not None and pd.notna(live_price) else float(r.close))
        market_regime,market_score,ihsg_alignment,regime_adjustment=_market_context(r)
"""
if new not in s:
    if old not in s: raise SystemExit('dynamic update time anchor missing')
    s=s.replace(old,new,1)

old="""            'close':float(r.close),'entry_low':lo,'entry_high':hi,
"""
new="""            'close':market_price,'entry_low':lo,'entry_high':hi,
"""
if new not in s:
    if old not in s: raise SystemExit('dynamic row close anchor missing')
    s=s.replace(old,new,1)

old="""            'decision':str(r.decision),'decision_reason':str(r.decision_reason),
"""
new="""            'decision':str(r.decision),'decision_reason':str(r.decision_reason),
            'price_source':str(ts.get('price_source') or 'DAILY_FALLBACK'),'price_at':str(ts.get('price_at') or ''),
"""
if new not in s:
    if old not in s: raise SystemExit('dynamic row decision anchor missing')
    s=s.replace(old,new,1)

old="""        reason=f\"{mode_note}score {rr.score:.1f}; IHSG {rr.market_regime} {rr.market_score:.1f}; alignment {rr.ihsg_alignment:.1f}; regime adj {rr.regime_adjustment:+.1f}; relative {rr.relative_percentile*100:.1f}%; time edge {ts.get('edge',0)*100:+.1f}%; {rr.decision}\"
"""
new="""        price_note=(f\"price {rr.price_source} {str(rr.price_at)[:19]}; \" if hasattr(rr,'price_source') else '')
        reason=f\"{mode_note}{price_note}score {rr.score:.1f}; IHSG {rr.market_regime} {rr.market_score:.1f}; alignment {rr.ihsg_alignment:.1f}; regime adj {rr.regime_adjustment:+.1f}; relative {rr.relative_percentile*100:.1f}%; time edge {ts.get('edge',0)*100:+.1f}%; {rr.decision}\"
"""
if new not in s:
    if old not in s: raise SystemExit('dynamic reason anchor missing')
    s=s.replace(old,new,1)

p.write_text(s,encoding='utf-8')
print('R27 INTRADAY FRESHNESS PATCH APPLIED')
