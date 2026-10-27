"""Shared saved-data checks. A refresh/fresh flag cannot certify old evidence."""
import math
import pandas as pd
import market_session
from timemap_data import canonical_ticker,clean


def stamp(value):
    try:
        t=pd.Timestamp(value)
        return t.tz_convert('Asia/Jakarta') if t.tzinfo is not None and not pd.isna(t) else None
    except (TypeError,ValueError):return None


def current_timestamp(value,session,max_minutes=5):
    now=stamp(session.get('wib'));t=stamp(value)
    if now is None or t is None or not session.get('live') or not session.get('calendar_verified'):return False
    actual=market_session.session_status(now=now)
    if not actual['live'] or actual['status']!=session.get('status'):return False
    return (t.date()==now.date() and str(session.get('market_date'))==now.date().isoformat()
            and market_session.session_status(now=t)['status']==actual['status']
            and 0 <= (now-t).total_seconds()/60 <= max_minutes)


def valid_live_info(ticker,info,session):
    try:
        source=str(info.get('source') or '').strip().upper()
        price=float(info.get('price'))
        return (bool(info.get('fresh')) and canonical_ticker(info.get('ticker'))==canonical_ticker(ticker)
                and math.isfinite(price) and price>0 and source not in {'','UNKNOWN','UNAVAILABLE','NONE','NAN'}
                and 'CACHE' not in source and current_timestamp(info.get('ts'),session))
    except (TypeError,ValueError):return False


def current_dynamic(ticker,row,session):
    try:
        return (canonical_ticker(row.get('ticker'))==canonical_ticker(ticker)
                and current_timestamp(row.get('generated_at'),session))
    except (TypeError,ValueError):return False


def read_intraday_context(connect,ticker,cfg=None,now=None):
    session=market_session.session_status(cfg,now=now)
    result={'live_required':bool(session.get('live')),'fresh':False,'price':None,'ts':'',
            'age_minutes':None,'market_date':str(session.get('market_date') or ''),
            'ticker':ticker,'source':'UNAVAILABLE','data_status':'DATA UNAVAILABLE'}
    if not session.get('live') or not session.get('calendar_verified'):return result
    con=None
    try:
        ticker=canonical_ticker(ticker);result['ticker']=ticker
        con=connect()
        d=pd.read_sql_query('SELECT * FROM intraday WHERE ticker=? ORDER BY julianday(ts) DESC LIMIT 1',con,params=(ticker,))
        if d.empty:return result
        r=d.iloc[0];result.update(ts=str(r.get('ts') or ''),source=str(r.get('source') or 'UNKNOWN'))
        if not current_timestamp(r.get('ts'),session):
            result['data_status']='DATA STALE';return result
        cleaned=clean(d,ticker,pd.Timestamp(session['wib']))
        if cleaned.empty or pd.isna(cleaned.iloc[0].volume) or cleaned.iloc[0].volume<=0:return result
        result.update(price=float(cleaned.iloc[0].close),fresh=True,
                      age_minutes=(pd.Timestamp(session['wib'])-pd.Timestamp(r.ts)).total_seconds()/60)
        if not valid_live_info(ticker,result,session):
            result.update(fresh=False,price=None,data_status='DATA STALE');return result
        result['data_status']='RECENT INTRADAY • FEED MAY BE DELAYED'
        return result
    except Exception:return result
    finally:
        if con is not None:con.close()
