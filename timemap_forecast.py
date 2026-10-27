"""Conditional day-analogue projections. Experimental shadow model, never BUY.

The model and fixed settings are independent of evaluation outcomes. Only prior
trading days supply labels; today's completed prefix/open supply conditioning.
"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from timemap_data import canonical_ticker,clean
from timemap_statistics import full_regular_bar
from market_session import session_status

VERSION='conditional-day-analogue-v1'
MIN_SAMPLES=12
THRESHOLD=.002
MODEL_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MODEL_ID=VERSION+'@'+MODEL_SHA256[:12]
FEATURES=['prior_return','prior_volatility','opening_gap','prefix_return','prefix_volume']


def schedule(now,cfg=None):
    now=pd.Timestamp(now).tz_convert('Asia/Jakarta')
    result=[]
    for minute in range(9*60,16*60+1,30):
        start=now.normalize()+pd.Timedelta(minutes=minute)
        result.append({'start':start,'end':min(start+pd.Timedelta(minutes=30),now.normalize()+pd.Timedelta(hours=16)),
                       'regular':full_regular_bar(start,cfg)})
    return result


def prepare(frame,ticker,now,cfg=None):
    d=clean(frame,ticker,now)
    if d.empty:return d
    local=d.ts.dt.tz_convert('Asia/Jakarta')
    d=d[(local.dt.minute%30==0)&(local.dt.second==0)&d.volume.notna()&(d.volume>0)].copy()
    d['day']=d.ts.dt.tz_convert('Asia/Jakarta').dt.date
    d['slot']=d.ts.dt.tz_convert('Asia/Jakarta').dt.strftime('%H:%M')
    d['ret']=d.close/d.open-1
    d['full']=[full_regular_bar(ts.tz_convert('Asia/Jakarta'),cfg) for ts in d.ts]
    return d


def _state(previous,prefix,opening):
    ret=previous.close/previous.open-1
    out=[float(previous.iloc[-1].close/previous.iloc[0].open-1)*100,
         float(np.sqrt(np.mean(np.square(ret))))*100]
    if opening is not None:
        out.append(float(opening/previous.iloc[-1].close-1)*100)
    if not prefix.empty:
        out.extend([float(prefix.iloc[-1].close/prefix.iloc[0].open-1)*100,
                    float(prefix.volume.mean()/previous.volume.mean())])
    return np.clip(np.asarray(out,dtype=float),-50,50)


def predict(frame,ticker,now,cfg=None,prepared=None,check_source=True):
    """Project still-future 30m intervals using information available at `now`.

    No inference is created retroactively for an interval already under way.
    Missing/stale inputs return an honest full-day WAIT schedule without numbers.
    """
    now=pd.Timestamp(now)
    if now.tzinfo is None:raise ValueError('Forecast requires aware time')
    now=now.tz_convert('Asia/Jakarta');ticker=canonical_ticker(ticker)
    cfg=cfg or {};session=session_status(cfg,now=now)
    slots=schedule(now,cfg)
    result={'ticker':ticker,'trading_date':now.date().isoformat(),'model_version':MODEL_ID,
            'generated_at':now.isoformat(),'rows':[], 'reason':'','actionable':False,
            'promotion':'SHADOW ONLY • NO EDGE','source':str(frame.attrs.get('source') or 'UNKNOWN')}
    def blocked(reason):
        result['reason']=reason
        result['rows']=[dict(start=x['start'].isoformat(),end=x['end'].isoformat(),
                            status='WAIT • '+reason,p=None,n=0) for x in slots]
        return result
    if not session['calendar_verified']:return blocked('KALENDER BELUM TERVERIFIKASI')
    if session['status'] not in {'PRE_MARKET','SESSION_1','SESSION_2','BREAK'}:
        return blocked('BUKAN SESI PROYEKSI HARI INI')
    try:raw=clean(frame,ticker,now)
    except ValueError:return blocked('IDENTITAS DATA TIDAK COCOK')
    if raw.empty:return blocked('DATA BELUM TERSEDIA')
    source=result['source'].upper()
    if check_source and (frame.attrs.get('fallback') or 'CACHE' in source or source in {'UNKNOWN','UNAVAILABLE','NONE','NAN'}):
        return blocked('SUMBER DATA BELUM SEGAR')
    latest=raw.ts.max().tz_convert('Asia/Jakarta')
    if session['status']=='PRE_MARKET':
        day=now.date()-timedelta(days=1)
        for _ in range(15):
            if session_status(cfg,now=pd.Timestamp(day,tz='Asia/Jakarta')+pd.Timedelta(hours=9,minutes=30))['live']:break
            day-=timedelta(days=1)
        if latest.date()!=day:return blocked('HISTORI SESI TERAKHIR BELUM TERSEDIA')
        phase='PROYEKSI PRE-MARKET • UJI BAYANGAN'
    else:
        if latest.date()!=now.date():return blocked('MENUNGGU DATA PERDAGANGAN HARI INI')
        latest_session=session_status(cfg,now=latest)
        if session['live'] and latest_session['status']!=session['status']:
            return blocked('MENUNGGU DATA SESI SAAT INI')
        if now-latest>pd.Timedelta(minutes=45):return blocked('DATA TERLALU LAMA')
        trades=raw[(raw.ts.dt.tz_convert('Asia/Jakarta').dt.date==now.date()) & raw.volume.notna() & (raw.volume>0)]
        if trades.empty:return blocked('BELUM ADA VOLUME TRANSAKSI HARI INI')
        phase='PROYEKSI DELAYED • UJI BAYANGAN'
    d=prepare(raw,ticker,now,cfg) if prepared is None else prepared.loc[prepared.ts<=now.tz_convert('UTC')].copy()
    if d.empty:return blocked('CANDLE REGULER BELUM CUKUP')
    # Prior-day states and labels must be fully in the past, not today's close.
    history=d[(d.day<now.date())&(d.ts>=now.tz_convert('UTC')-pd.Timedelta(days=59))&d.full]
    days=sorted(history.day.unique())
    if len(days)<MIN_SAMPLES+1:return blocked('INSUFFICIENT DATA • MINIMAL 13 HARI')
    groups={day:group.sort_values('ts') for day,group in history.groupby('day')}
    previous=groups[days[-1]]
    today=d[(d.day==now.date())&d.full]
    # A delayed provider may still carry an unfinished 09:00 bar at 09:35.
    # Require a later provider observation, not wall clock alone, to close it.
    watermark=min(now.tz_convert('UTC'),raw.ts.max())
    prefix=today[today.ts+pd.Timedelta(minutes=30)<=watermark].sort_values('ts')
    known_slots=prefix.slot.tolist()
    opening_rows=today[today.slot=='09:00'] if session['status']!='PRE_MARKET' else today.iloc[:0]
    opening=float(opening_rows.iloc[0].open) if len(opening_rows) else None
    current=_state(previous,prefix,opening)
    vectors={};labels={}
    for i,day in enumerate(days[1:],1):
        old=groups[day];prior=groups[days[i-1]]
        old_prefix=old[old.slot.isin(known_slots)].sort_values('ts')
        if set(old_prefix.slot)!=set(known_slots):continue
        old_open=old[old.slot=='09:00']
        if opening is not None and old_open.empty:continue
        vectors[day]=_state(prior,old_prefix,float(old_open.iloc[0].open) if opening is not None else None)
        labels[day]={r.slot:(2 if r.ret>THRESHOLD else 0 if r.ret < -THRESHOLD else 1) for r in old.itertuples()}
    feature_asof=max([previous.ts.max()+pd.Timedelta(minutes=30)]+
                     ([prefix.ts.max()+pd.Timedelta(minutes=30)] if len(prefix) else [])+
                     ([opening_rows.ts.min()] if opening is not None else []))
    identity={'ticker':ticker,'day':str(now.date()),'version':MODEL_ID,'current':current.tolist(),
              'prefix':known_slots,'vectors':[(str(day),v.tolist(),labels[day]) for day,v in vectors.items()]}
    result.update(input_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
                  feature_asof=feature_asof.isoformat(),training_end=str(days[-1]),
                  latest_candle=latest.isoformat(),phase=phase,feature_values=current.tolist())
    for slot in slots:
        row=dict(start=slot['start'].isoformat(),end=slot['end'].isoformat(),p=None,n=0,status='WAIT')
        if not slot['regular']:row['status']='JEDA / BUKAN INTERVAL REGULER 30M'
        elif slot['start']<now:row['status']='SUDAH DIMULAI • TIDAK MEMBUAT PREDIKSI SUSULAN'
        else:
            key=slot['start'].strftime('%H:%M')
            eligible=[day for day in vectors if key in labels[day]]
            row['n']=len(eligible)
            if len(eligible)<MIN_SAMPLES:row['status']='INSUFFICIENT DATA'
            else:
                x=np.asarray([vectors[day] for day in eligible]); y=np.asarray([labels[day][key] for day in eligible])
                scale=x.std(axis=0);scale[scale<1e-6]=1
                weights=1/(1+np.mean(np.square((x-current)/scale),axis=1))
                weights=weights/weights.mean()
                baseline=(np.bincount(y,minlength=3)+1)/(len(y)+3)
                p=.7*(np.bincount(y,weights=weights,minlength=3)+1)/(weights.sum()+3)+.3*baseline
                row.update(p=p.tolist(),baseline=baseline.tolist(),status=phase+' • NO EDGE')
        result['rows'].append(row)
    return result
