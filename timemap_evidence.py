"""Bounded, first-issued forecast diagnostics. Never a promotion or BUY gate."""
import json
import sqlite3
import numpy as np
import pandas as pd
from timemap_data import canonical_ticker
from timemap_forecast import MODEL_ID,THRESHOLD
from timemap_statistics import full_regular_bar


def _stamp(value):
    value=pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:raise ValueError('Aware timestamp required')
    return value.tz_convert('Asia/Jakarta')


def _source(value):
    value=str(value or '').strip().upper()
    return value not in {'','UNKNOWN','UNAVAILABLE','NONE','NAN'} and 'CACHE' not in value


def _metrics(rows):
    if not rows:return {'n':0,'direction_n':0,'correct':0,'accuracy_pct':None,'balanced_accuracy_pct':None,
                        'precision_up_pct':None,'precision_down_pct':None,'brier':None,'ece':None}
    p=np.asarray([r['p'] for r in rows]);y=np.asarray([r['y'] for r in rows]);pred=p.argmax(axis=1)
    sorted_p=np.sort(p,axis=1);direction=~np.isclose(sorted_p[:,-1],sorted_p[:,-2])
    hits=(pred==y)&direction
    recalls=[float(hits[(y==i)&direction].mean()) for i in range(3) if ((y==i)&direction).any()]
    def precision(label):
        mask=(pred==label)&direction
        return float((y[mask]==label).mean()*100) if mask.any() else None
    ece=0.;confidence=p.max(axis=1)
    for lo in np.arange(0,1,.1):
        mask=(confidence>=lo)&(confidence<lo+.1 if lo<.9 else confidence<=1)&direction
        if mask.any():ece+=int(mask.sum())*abs(float(confidence[mask].mean())-float(hits[mask].mean()))
    return {'n':len(rows),'direction_n':int(direction.sum()),'correct':int(hits.sum()),
            'accuracy_pct':float(hits[direction].mean()*100) if direction.any() else None,
            'balanced_accuracy_pct':float(np.mean(recalls)*100) if recalls else None,
            'precision_up_pct':precision(2),'precision_down_pct':precision(0),
            'brier':float(((p-np.eye(3)[y])**2).sum(axis=1).mean()),
            'ece':ece/int(direction.sum()) if direction.any() else None}


def summarize(records,ticker,now,model_id=MODEL_ID):
    """Count each original interval once; never substitute a winning revision."""
    ticker=canonical_ticker(ticker);now=_stamp(now);groups={};rejected=0;pending=0;valid=0;scored=[]
    for row in records:
        if row.get('ticker')!=ticker or row.get('model_version')!=model_id:continue
        key=(row.get('trading_date'),row.get('slot'))
        groups.setdefault(key,[]).append(row)
    for group in groups.values():
        try:
            # Ambiguous issue times invalidate the interval, rather than allowing
            # a later, better revision to replace the unknown first forecast.
            group=sorted(group,key=lambda r:(_stamp(r['generated_at']).value,str(r['input_hash'])))
            row=group[0];issued=_stamp(row['generated_at']);slot=_stamp(row['slot'])
            feature=_stamp(row['feature_asof']);candle=_stamp(row['latest_candle'])
            if (slot.date().isoformat()!=row['trading_date'] or issued.date()!=slot.date()
                or issued>slot or issued>now or feature>issued or candle>issued
                or pd.Timestamp(row['training_end']).date()>=slot.date()
                or slot<now.normalize()-pd.Timedelta(days=59) or slot.date()>now.date()
                or not _source(row.get('source')) or not full_regular_bar(slot)):raise ValueError('Invalid evidence identity')
            p=np.asarray(json.loads(row['probabilities']),dtype=float)
            if p.shape!=(3,) or not np.isfinite(p).all() or (p<0).any() or (p>1).any() or not np.isclose(p.sum(),1):
                raise ValueError('Invalid probability')
            valid+=1
            if row.get('actual_label') is None:pending+=1;continue
            settled=_stamp(row['evaluated_at'])
            if settled>now or slot+pd.Timedelta(minutes=30)>now:pending+=1;continue
            if settled<slot+pd.Timedelta(minutes=30) or not _source(row.get('outcome_source')):
                raise ValueError('Unverified outcome')
            y=int(row['actual_label']);ret=float(row['return_pct'])
            expected=2 if ret>THRESHOLD*100 else 0 if ret < -THRESHOLD*100 else 1
            if y not in (0,1,2) or not np.isfinite(ret) or y!=expected:raise ValueError('Invalid outcome label')
            scored.append({'day':slot.date().isoformat(),'p':p.tolist(),'y':y,'slot':slot.isoformat()})
        except (KeyError,TypeError,ValueError,OverflowError):rejected+=1
    days=sorted({r['day'] for r in scored});metrics=_metrics(scored)
    recent_days=set(days[-5:]);recent=[r for r in scored if r['day'] in recent_days]
    earlier=[r for r in scored if r['day'] not in recent_days]
    recent_metrics=_metrics(recent);earlier_metrics=_metrics(earlier)
    drift='BELUM CUKUP DATA'
    if len(days)>=10 and len(recent)>=30 and len(earlier)>=30:
        drift=('PERFORMA MENURUN — PERLU DITINJAU' if recent_metrics['brier']-earlier_metrics['brier']>=.05
               else 'BELUM TERDETEKSI PENURUNAN BESAR')
    return dict(ticker=ticker,model_id=model_id,**metrics,days=len(days),start=days[0] if days else None,
        end=days[-1] if days else None,pending=pending,rejected=rejected,valid_forecasts=valid,
        recent=recent_metrics,earlier=earlier_metrics,drift=drift,
        status='SAMPEL BELUM CUKUP' if len(scored)<200 or len(days)<20 else 'NO EDGE — BELUM BUKTI KEUNGGULAN',
        approved=False,uniform_brier=2/3)


def read_evidence(path,ticker,now):
    """Read only the selected ticker/current model within a bounded 60-day window."""
    ticker=canonical_ticker(ticker);now=_stamp(now);con=sqlite3.connect(path,timeout=5)
    try:
        con.row_factory=sqlite3.Row
        rows=con.execute('''SELECT f.*,o.actual_label,o.return_pct,o.evaluated_at,o.source AS outcome_source
            FROM timemap_forecast_log f LEFT JOIN timemap_forecast_outcomes o USING(ticker,trading_date,slot)
            WHERE f.ticker=? AND f.model_version=? AND f.trading_date>=? AND f.trading_date<=?
            ORDER BY julianday(f.generated_at),f.input_hash''',
            (ticker,MODEL_ID,(now.normalize()-pd.Timedelta(days=59)).date().isoformat(),now.date().isoformat())).fetchall()
        return summarize([dict(r) for r in rows],ticker,now)
    finally:con.close()
