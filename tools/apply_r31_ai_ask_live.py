from pathlib import Path

path=Path('app.py')
src=path.read_text(encoding='utf-8')

insert_at=src.index('def stock_search_panel(')
if 'def build_ai_ask_snapshot(' not in src:
    helper='''def build_ai_ask_snapshot(universal_row, dynamic_row, ticker, session=None, live_info=None):
    """Build AI Ask input with explicit authority: live Dynamic > daily snapshot."""
    def as_dict(value):
        if value is None:
            return {}
        if isinstance(value,dict):
            return dict(value)
        if hasattr(value,'to_dict'):
            try:return dict(value.to_dict())
            except Exception:return {}
        return {}

    snapshot=as_dict(universal_row)
    dynamic=as_dict(dynamic_row)
    authoritative=(
        'close','entry_low','entry_high','stop_loss','target_1','target_2',
        'entry_status','state','candidate_mode','decision','ai_score','risk',
        'market_regime','market_score','p_up','p_down','confidence'
    )
    for key in authoritative:
        if key not in dynamic:
            continue
        value=dynamic.get(key)
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        if value is not None:
            snapshot[key]=value

    snapshot['ticker']=str(ticker).upper().replace('.JK','').strip()
    session=dict(session or market_session.session_status(CFGD) or {})
    snapshot['market_status']=str(session.get('status') or '').upper()
    snapshot['live_required']=bool(session.get('live'))

    if live_info is None:
        try:
            import dynamic_engine as _dynamic_engine
            live_info=_dynamic_engine._latest_intraday_live_price(snapshot['ticker'])
        except Exception:
            live_info={}
    live_info=dict(live_info or {})
    snapshot['intraday_fresh']=bool(snapshot['live_required'] and live_info.get('fresh'))
    if snapshot['intraday_fresh'] and live_info.get('price') is not None:
        try:snapshot['close']=float(live_info.get('price'))
        except Exception:pass
        snapshot['price_source']='LIVE_INTRADAY'
        snapshot['intraday_at']=str(live_info.get('ts') or '')
    else:
        snapshot['price_source']=str(dynamic.get('price_source') or ('STALE_INTRADAY' if snapshot['live_required'] else 'DAILY'))
        snapshot['intraday_at']=str(live_info.get('ts') or '')
    return snapshot

'''
    src=src[:insert_at]+helper+src[insert_at:]

old='''    ask_snapshot={} if ur is None else ur.to_dict()
    if dr is not None:
        for k,v in dr.to_dict().items():
            if k not in ask_snapshot or pd.isna(ask_snapshot.get(k)):
                ask_snapshot[k]=v
    ask_snapshot['ticker']=selected_ticker
    ask=ai_stock_ask.answer_stock(ask_snapshot,ask_horizon)
'''
new='''    ask_snapshot=build_ai_ask_snapshot(ur,dr,selected_ticker)
    ask=ai_stock_ask.answer_stock(ask_snapshot,ask_horizon)
'''
if old not in src:
    raise SystemExit('AI Ask assembly anchor missing')
src=src.replace(old,new,1)
path.write_text(src,encoding='utf-8')
print('R31 AI STOCK ASK LIVE PATCH APPLIED')
