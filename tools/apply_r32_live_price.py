from pathlib import Path

# R32: make Hold + stock-detail price surfaces explicit about live vs daily data.
hold_path=Path('hold_engine.py')
h=hold_path.read_text(encoding='utf-8')

if 'import market_session\n' not in h:
    h=h.replace('import app\n','import app\nimport market_session\n',1)

old_cols="    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_effect','REAL')]:\n"
new_cols="    for col,typ in [('market_regime','TEXT'),('market_score','REAL'),('ihsg_alignment','REAL'),('regime_effect','REAL'),('price_source','TEXT'),('intraday_fresh','INTEGER'),('intraday_at','TEXT'),('time_status','TEXT')]:\n"
if old_cols not in h:
    raise SystemExit('hold schema migration anchor missing')
h=h.replace(old_cols,new_cols,1)

start=h.index('def _time(ticker):')
end=h.index('\ndef _ihsg_context():',start)
new_time=r'''def _live_intraday_context(ticker, now=None):
    session=market_session.session_status(CFG,now=now)
    live=bool(session.get('live'))
    market_date=str(session.get('market_date') or '')
    result={'live_required':live,'fresh':False,'price':None,'ts':'','age_minutes':None,'market_date':market_date}
    if not live:
        return result
    try:
        con=dbcon()
        d=pd.read_sql_query(
            'SELECT ts,close FROM intraday WHERE ticker=? ORDER BY ts DESC LIMIT 20',
            con,params=(str(ticker).upper().replace('.JK',''),)
        )
        con.close()
        if d.empty:return result
        d['ts']=pd.to_datetime(d['ts'],utc=True,errors='coerce')
        d=d.dropna(subset=['ts','close'])
        if d.empty:return result
        d['wib']=d['ts'].dt.tz_convert('Asia/Jakarta')
        d=d[d['wib'].dt.date.astype(str)==market_date]
        if d.empty:return result
        row=d.sort_values('ts').iloc[-1]
        local_now=pd.Timestamp(session.get('wib'))
        if local_now.tzinfo is None:local_now=local_now.tz_localize('Asia/Jakarta')
        age=max(0.0,(local_now.tz_convert('UTC')-row['ts']).total_seconds()/60.0)
        limit=max(45,int(CFG.get('intraday_live_stale_minutes',45)),int(CFG.get('intraday_refresh_minutes',30))+10)
        result.update({'fresh':age<=limit,'price':float(row['close']),'ts':row['ts'].isoformat(),'age_minutes':age})
        return result
    except Exception:
        try:con.close()
        except Exception:pass
        return result


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
'''
h=h[:start]+new_time+h[end:]

old_loop="        ts=_time(r.ticker); dc=_daily_context(r.ticker); hs=_hold_score(r,ts,dc,market_ctx); ov, bias, os=_overnight(r,ts,dc,phase,market_ctx)\n"
new_loop="        ts=_time(r.ticker); dc=_daily_context(r.ticker); hs=_hold_score(r,ts,dc,market_ctx); ov, bias, os=_overnight(r,ts,dc,phase,market_ctx)\n        current_price=float(ts['live_price']) if ts.get('live_price') is not None else float(r.close)\n"
if old_loop not in h:raise SystemExit('hold update loop anchor missing')
h=h.replace(old_loop,new_loop,1)

old_reason="        reason=f'Hold score {hs:.1f}; P(UP) {float(r.jst_p_up)*100:.1f}%; P(DOWN) {float(r.jst_p_down)*100:.1f}%; time edge {ts[\"edge\"]*100:+.1f}%; overnight {ov}.'\n"
new_reason="        live_note='intraday FRESH' if ts.get('intraday_fresh') else ('intraday STALE/NEUTRAL' if ts.get('live_required') else 'off-market/historical')\n        reason=f'Hold score {hs:.1f}; P(UP) {float(r.jst_p_up)*100:.1f}%; P(DOWN) {float(r.jst_p_down)*100:.1f}%; time edge {ts[\"edge\"]*100:+.1f}%; {live_note}; overnight {ov}.'\n"
if old_reason not in h:raise SystemExit('hold reason anchor missing')
h=h.replace(old_reason,new_reason,1)

old_row="'market_regime':market_ctx[0],'market_score':market_ctx[1],'ihsg_alignment':ihsg_alignment,'regime_effect':regime_effect})\n"
new_row="'market_regime':market_ctx[0],'market_score':market_ctx[1],'ihsg_alignment':ihsg_alignment,'regime_effect':regime_effect,'price_source':str(ts.get('price_source') or 'DAILY_CLOSE'),'intraday_fresh':1 if ts.get('intraday_fresh') else 0,'intraday_at':str(ts.get('intraday_at') or ''),'time_status':str(ts.get('status') or 'NEUTRAL')})\n"
if old_row not in h:raise SystemExit('hold row anchor missing')
h=h.replace("'current_price':float(r.close)","'current_price':current_price",1)
h=h.replace(old_row,new_row,1)

old_sql="con.execute('''INSERT OR REPLACE INTO hold_intelligence (ticker,company,generated_at,phase,current_price,state,hold_horizon,hold_score,p_hold,p_up,p_down,confidence,time_edge,risk,stop_loss,target_1,target_2,risk_reward,invalidation,overnight_state,next_session_bias,overnight_score,reason,last_opportunity_score,opportunity_rank,market_regime,market_score,ihsg_alignment,regime_effect) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(r.ticker,r.company,now,phase,r.current_price,r.state,r.hold_horizon,r.hold_score,r.p_hold,r.p_up,r.p_down,r.confidence,r.time_edge,r.risk,r.stop_loss,r.target_1,r.target_2,r.risk_reward,r.invalidation,r.overnight_state,r.next_session_bias,r.overnight_score,r.reason,r.last_opportunity_score,r.opportunity_rank,r.market_regime,r.market_score,r.ihsg_alignment,r.regime_effect))"
new_sql="con.execute('''INSERT OR REPLACE INTO hold_intelligence (ticker,company,generated_at,phase,current_price,state,hold_horizon,hold_score,p_hold,p_up,p_down,confidence,time_edge,risk,stop_loss,target_1,target_2,risk_reward,invalidation,overnight_state,next_session_bias,overnight_score,reason,last_opportunity_score,opportunity_rank,market_regime,market_score,ihsg_alignment,regime_effect,price_source,intraday_fresh,intraday_at,time_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(r.ticker,r.company,now,phase,r.current_price,r.state,r.hold_horizon,r.hold_score,r.p_hold,r.p_up,r.p_down,r.confidence,r.time_edge,r.risk,r.stop_loss,r.target_1,r.target_2,r.risk_reward,r.invalidation,r.overnight_state,r.next_session_bias,r.overnight_score,r.reason,r.last_opportunity_score,r.opportunity_rank,r.market_regime,r.market_score,r.ihsg_alignment,r.regime_effect,r.price_source,int(r.intraday_fresh),r.intraday_at,r.time_status))"
if old_sql not in h:raise SystemExit('hold insert SQL anchor missing')
h=h.replace(old_sql,new_sql,1)
hold_path.write_text(h,encoding='utf-8')

# App: make top price metric explicit and use same fresh-live rule.
app_path=Path('app.py')
a=app_path.read_text(encoding='utf-8')
insert_at=a.index('def stock_search_panel(')
if 'def resolve_stock_display_price(' not in a:
    helper='''def resolve_stock_display_price(ticker, daily_close, session=None, live_info=None):
    session=dict(session or market_session.session_status(CFGD) or {})
    live_required=bool(session.get('live'))
    if live_info is None:
        try:
            import dynamic_engine as _dynamic_engine
            live_info=_dynamic_engine._latest_intraday_live_price(str(ticker))
        except Exception:
            live_info={}
    live_info=dict(live_info or {})
    if live_required and live_info.get('fresh') and live_info.get('price') is not None:
        try:price=float(live_info.get('price'))
        except Exception:price=float(daily_close)
        return {'price':price,'label':'Harga live','source':'LIVE_INTRADAY','fresh':True,'at':str(live_info.get('ts') or '')}
    return {'price':float(daily_close),'label':'Daily close','source':'DAILY_CLOSE','fresh':False,'at':''}

'''
    a=a[:insert_at]+helper+a[insert_at:]

old_metric="    p1,p2,p3,p4,p5=st.columns(5)\n    p1.metric('Harga terakhir',f'Rp {close:,.0f}')\n"
new_metric="    display_price=resolve_stock_display_price(selected_ticker,close)\n    p1,p2,p3,p4,p5=st.columns(5)\n    p1.metric(display_price['label'],f\"Rp {float(display_price['price']):,.0f}\")\n"
if old_metric not in a:raise SystemExit('app price metric anchor missing')
a=a.replace(old_metric,new_metric,1)
old_caption="    st.caption(f'Data harga terakhir: {r.date} • source={latest_source} • updated={latest_updated}')\n"
new_caption="    live_note=(f\" • live_at={display_price['at']}\" if display_price.get('fresh') else '')\n    st.caption(f\"Sumber harga tampilan: {display_price['source']}{live_note} • daily candle: {r.date} • daily_source={latest_source} • updated={latest_updated}\")\n"
if old_caption not in a:raise SystemExit('app price caption anchor missing')
a=a.replace(old_caption,new_caption,1)
app_path.write_text(a,encoding='utf-8')
print('R32 LIVE PRICE COHERENCE PATCH APPLIED')
