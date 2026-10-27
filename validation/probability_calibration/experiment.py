"""Chronological calibration diagnostic. No production import or model promotion."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from timemap_forecast import predict,prepare,MODEL_ID
from validation.quality.conditional_models import metrics
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent


def archive(ticker):
    dirs=['validation/acceptance/inputs','validation/reliability/inputs','validation/expanded/inputs',
          'validation/expanded/recovered','validation/engine_upgrade','validation/broad_tickers/inputs']
    files=[ROOT/d/f'{ticker}.csv' for d in dirs if (ROOT/d/f'{ticker}.csv').exists()]
    frame=pd.concat([pd.read_csv(p) for p in files],ignore_index=True) if files else pd.DataFrame()
    frame.attrs={'ticker':ticker,'source':'ARCHIVE REPLAY'}
    return frame,{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def replay(raw,ticker,dates,clocks):
    out=[]
    prepared=prepare(raw,ticker,pd.Timestamp(max(dates),tz='Asia/Jakarta')+pd.Timedelta(days=1))
    if prepared.empty:return out
    for day in dates:
        targets=prepared[(prepared.day.astype(str)==day)&prepared.full].set_index('slot')
        for clock in clocks:
            now=pd.Timestamp(day+' '+clock,tz='Asia/Jakarta')
            bundle=predict(raw,ticker,now,prepared=prepared,check_source=False)
            for r in bundle['rows']:
                slot=pd.Timestamp(r['start']).strftime('%H:%M')
                if r['p'] is None or slot not in targets.index:continue
                ret=float(targets.loc[slot].ret)
                past=prepared[(prepared.day.astype(str)<day)&prepared.slot.eq(slot)&prepared.full]
                y=np.where(past.ret.to_numpy()>.002,2,np.where(past.ret.to_numpy()<-.002,0,1))
                baseline=(np.bincount(y,minlength=3)+1)/(len(y)+3)
                out.append(dict(ticker=ticker,day=day,issue=clock,slot=slot,p=r['p'],baseline=baseline.tolist(),
                    actual=2 if ret>.002 else 0 if ret<-.002 else 1,feature_asof=bundle['feature_asof'],
                    generated_at=bundle['generated_at'],training_end=bundle['training_end']))
    return out


def choose(rows,weights):
    if not rows:raise ValueError('No validation observations')
    scores={}
    for alpha in weights:
        per_day={}
        for r in rows:
            p=alpha*np.asarray(r['p'])+(1-alpha)/3
            loss=float(((p-np.eye(3)[r['actual']])**2).sum())
            per_day.setdefault(r['day'],[]).append(loss)
        scores[str(alpha)]=float(np.mean([np.mean(v) for v in per_day.values()]))
    return min(weights,key=lambda w:(scores[str(w)],w)),scores


def freeze():
    protocol=json.loads((HERE/'protocol.json').read_text());rows=[];hashes={}
    for t in protocol['tickers']:
        raw,hashes[t]=archive(t)
        if not raw.empty:rows+=replay(raw,t,protocol['validation_dates'],protocol['issue_times'])
    alpha,scores=choose(rows,protocol['model_weights'])
    manifest=dict(alpha=alpha,validation_scores=scores,n_validation=len(rows),model_id=MODEL_ID,
        frozen_at=pd.Timestamp.now(tz='UTC').isoformat(),production_enabled=False,
        protocol_sha256=hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
        engine_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),archive_sha256=hashes)
    (HERE/'frozen.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest))


def score(rows,probabilities):
    if not rows:return {'n':0}
    y=np.asarray([r['actual'] for r in rows]);p=np.asarray(probabilities)
    result=metrics(y,p)
    # Uniform ties express no direction; never count argmax's DOWN as a signal.
    tied=np.isclose(np.sort(p,axis=1)[:,-1],np.sort(p,axis=1)[:,-2])
    result['unique_direction_n']=int((~tied).sum())
    if tied.all():
        for key in ['directional_accuracy','balanced_accuracy','precision_up','precision_down','ece']:
            result[key]=None
    ordered=np.sort(p,axis=1);eligible=(ordered[:,-1]>=.45)&((ordered[:,-1]-ordered[:,-2])>=.10)&~tied
    result['filtered_n']=int(eligible.sum())
    result['filtered_accuracy']=float((p[eligible].argmax(axis=1)==y[eligible]).mean()) if eligible.any() else None
    return result


def evaluate():
    protocol=json.loads((HERE/'protocol.json').read_text());manifest=json.loads((HERE/'frozen.json').read_text())
    if manifest['protocol_sha256']!=hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest():raise ValueError('Protocol changed')
    if manifest['engine_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Engine changed')
    if manifest['model_id']!=MODEL_ID:raise ValueError('Base model changed')
    acquisition=json.loads((HERE/'acquisition.json').read_text())
    if pd.Timestamp(manifest['frozen_at'])>=pd.Timestamp(acquisition['started_at']):raise ValueError('Test bars acquired before freeze')
    rows=[];coverage=[]
    for t in protocol['tickers']:
        raw,hashes=archive(t)
        if hashes!=manifest['archive_sha256'][t]:raise ValueError('Archive changed')
        path=HERE/'inputs'/f'{t}.csv'
        if not path.exists():coverage.append({'ticker':t,'n':0,'status':'DATA UNAVAILABLE'});continue
        fresh=pd.read_csv(path)
        combined=pd.concat([raw,fresh],ignore_index=True);combined.attrs={'ticker':t,'source':'ARCHIVE + NEW PROVIDER REPLAY'}
        rr=replay(combined,t,[protocol['test_date']],protocol['issue_times']);rows+=rr
        coverage.append({'ticker':t,'n':len(rr),'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    alpha=manifest['alpha'];p=[(alpha*np.asarray(r['p'])+(1-alpha)/3).tolist() for r in rows]
    report={'alpha_frozen':alpha,'checked_at':pd.Timestamp.now(tz='UTC').isoformat(),'coverage':coverage,
        'n':len(rows),'unique_outcomes':len({(r['ticker'],r['day'],r['slot']) for r in rows}),
        'test_dates':[protocol['test_date']],
        'candidate':score(rows,p),'original':score(rows,[r['p'] for r in rows]),
        'r90':score(rows,[r['baseline'] for r in rows]),'uniform_brier':2/3,
        'by_ticker':{},'production_enabled':False,'reason':protocol['promotion'],
        'limitations':protocol['evaluation_warning']}
    for t in protocol['tickers']:
        ids=[i for i,r in enumerate(rows) if r['ticker']==t];rr=[rows[i] for i in ids]
        report['by_ticker'][t]={'candidate':score(rr,[p[i] for i in ids]),'original':score(rr,[r['p'] for r in rr]),'r90':score(rr,[r['baseline'] for r in rr])}
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    (HERE/'replay.json').write_text(json.dumps([dict(r,calibrated=p[i]) for i,r in enumerate(rows)],indent=2))
    print(json.dumps(report))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['freeze','evaluate'])
    args=parser.parse_args();freeze() if args.mode=='freeze' else evaluate()
