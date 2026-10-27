"""Fixed retrospective walk-forward diagnostic; no tuning or promotion."""
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
from timemap_forecast import predict,prepare,VERSION,MODEL_SHA256,MODEL_ID
from validation.quality.conditional_models import metrics

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent


def run():
    started=time.perf_counter();protocol=json.loads((HERE/'protocol.json').read_text())
    records=[];coverage=[];hashes={}
    cutoff=pd.Timestamp.now(tz='UTC')
    for ticker in protocol['tickers']:
        directories=['validation/acceptance/inputs','validation/reliability/inputs','validation/expanded/inputs',
                     'validation/expanded/recovered','validation/engine_upgrade','validation/broad_tickers/inputs']
        files=[ROOT/d/f'{ticker}.csv' for d in directories if (ROOT/d/f'{ticker}.csv').exists()]
        if not files:
            coverage.append({'ticker':ticker,'days':0,'included':False});continue
        hashes[ticker]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        raw=pd.concat([pd.read_csv(p) for p in files],ignore_index=True);raw.attrs={'ticker':ticker,'source':'ARCHIVE REPLAY'}
        d=prepare(raw,ticker,cutoff);days=sorted(d.day.unique()) if not d.empty else []
        included=len(days)>=35
        coverage.append({'ticker':ticker,'raw_rows':len(raw),'eligible_rows':len(d),'days':len(days),'included':included})
        if not included:continue
        for day in days[-protocol['test_days_per_ticker']:]:
            for clock in protocol['issue_times_wib']:
                now=pd.Timestamp(f'{day} {clock}',tz='Asia/Jakarta')
                bundle=predict(raw,ticker,now,prepared=d,check_source=False)
                targets=d[(d.day==day)&d.full].set_index('slot')
                for row in bundle['rows']:
                    if row['p'] is None:continue
                    slot=pd.Timestamp(row['start']).strftime('%H:%M')
                    if slot not in targets.index:continue
                    target=targets.loc[slot];ret=float(target.ret)
                    past=d[(d.day<day)&d.slot.eq(slot)&d.full]
                    y=np.where(past.ret.to_numpy()>.002,2,np.where(past.ret.to_numpy() < -.002,0,1))
                    r90=((np.bincount(y,minlength=3)+1)/(len(y)+3)).tolist()
                    records.append({'ticker':ticker,'day':str(day),'issue':clock,'slot':slot,
                                    'generated_at':bundle['generated_at'],'feature_asof':bundle['feature_asof'],
                                    'training_end':bundle['training_end'],'actual':2 if ret>.002 else 0 if ret < -.002 else 1,
                                    'p':row['p'],'baseline':r90,'n_train':row['n']})
    def score(rows,key):
        return metrics([r['actual'] for r in rows],np.asarray([r[key] for r in rows])) if rows else {'n':0}
    report={'model_version':MODEL_ID,'model_sha256':MODEL_SHA256,'checked_at':pd.Timestamp.now(tz='UTC').isoformat(),
            'protocol_sha256':hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
            'coverage':coverage,'input_sha256':hashes,'forecast_instances':len(records),
            'unique_outcomes':len({(r['ticker'],r['day'],r['slot']) for r in records}),
            'model':score(records,'p'),'slot_baseline':score(records,'baseline'),'baseline_definition':'R90 same-ticker same-slot Laplace probabilities from all strictly prior eligible days','uniform_brier':2/3,
            'by_ticker':{t:{'model':score([r for r in records if r['ticker']==t],'p'),
                            'baseline':score([r for r in records if r['ticker']==t],'baseline')} for t in protocol['tickers']},
            'by_issue':{s:{'model':score([r for r in records if r['issue']==s],'p'),
                           'baseline':score([r for r in records if r['issue']==s],'baseline')} for s in protocol['issue_times_wib']},
            'promotion':'SHADOW ONLY; previously inspected archives, no prospective proof or cost evaluation',
            'elapsed_seconds':round(time.perf_counter()-started,3)}
    if records:
        y=np.asarray([r['actual'] for r in records]);truth=np.eye(3)[y]
        model_loss=((np.asarray([r['p'] for r in records])-truth)**2).sum(axis=1)
        report['day_block_brier_gain_95pct']={}
        for name,prob in [('R90',np.asarray([r['baseline'] for r in records])),('uniform',np.full((len(records),3),1/3))]:
            loss=((prob-truth)**2).sum(axis=1)
            daily=pd.DataFrame({'day':[r['day'] for r in records],'gain':loss-model_loss}).groupby('day').gain.mean().to_numpy()
            means=np.random.default_rng(91).choice(daily,size=(2000,len(daily)),replace=True).mean(axis=1)
            report['day_block_brier_gain_95pct'][name]=np.quantile(means,[.025,.975]).tolist()
    report['test_dates']=sorted({r['day'] for r in records})
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    (HERE/'replay.json').write_text(json.dumps(records,indent=2))
    print(json.dumps({k:report[k] for k in ['coverage','forecast_instances','unique_outcomes','model','slot_baseline','test_dates','elapsed_seconds']}))
    return report

if __name__=='__main__':run()
