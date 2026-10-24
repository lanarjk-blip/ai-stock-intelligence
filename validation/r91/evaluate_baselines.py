"""Reproducible untouched-test comparison; does not promote a production model."""
import json
from pathlib import Path
import numpy as np
import pandas as pd


def evaluate(path):
    d=pd.read_csv(path)
    if d.empty:
        return {'ticker':path.stem.split('-')[0],'status':'NO DATA'}
    d['ts']=pd.to_datetime(d.ts,utc=True)
    d=d.sort_values('ts').drop_duplicates('ts')
    local=d.ts.dt.tz_convert('Asia/Jakarta')
    minute=local.dt.hour*60+local.dt.minute
    regular=((minute>=540)&(minute<np.where(local.dt.dayofweek==4,690,720)))|((minute>=np.where(local.dt.dayofweek==4,840,810))&(minute<950))
    d=d[regular & (local.dt.dayofweek<5)].copy()
    d['day']=d.ts.dt.tz_convert('Asia/Jakarta').dt.date
    d['slot']=d.ts.dt.tz_convert('Asia/Jakarta').dt.strftime('%H:%M')
    ret=d.close/d.open-1
    d['y']=np.where(ret>.002,2,np.where(ret<-.002,0,1))
    d['previous']=d.y.shift(1).fillna(1).astype(int)
    days=sorted(d.day.unique());a=int(len(days)*.6);b=int(len(days)*.8)
    train=d[d.day.isin(days[:a])];valid=d[d.day.isin(days[a:b])];test=d[d.day.isin(days[b:])]
    def predict(part,kind):
        result=[]
        for _,r in part.iterrows():
            pool=train if kind=='global' else train[train.slot==r.slot]
            if kind=='conditional':
                subset=pool[pool.previous==r.previous]
                if len(subset)>=8:pool=subset
            counts=np.bincount(pool.y,minlength=3)
            result.append((counts+1)/(sum(counts)+3))
        return np.array(result)
    def score(part,p):
        return {'n':len(part),'accuracy':float((p.argmax(1)==part.y.to_numpy()).mean()),'brier':float(((p-np.eye(3)[part.y])**2).sum(1).mean())}
    candidates=['slot','global','conditional']
    validation={k:score(valid,predict(valid,k)) for k in candidates}
    selected=min(candidates,key=lambda k:validation[k]['brier'])
    # Select once on validation. Test is reporting-only, never used to reselect.
    results={k:score(test,predict(test,k)) for k in ['slot',selected]}
    return {'ticker':path.stem.split('-')[0],'days':len(days),'bars':len(d),'train_end':str(days[a-1]),'validation_end':str(days[b-1]),'test_start':str(days[b]),'test_end':str(days[-1]),'selected_on_validation':selected,'validation':validation,'untouched_test':results,'production_promotion':False,'note':'slot comparator uses frozen training counts; not an exact replay of R90 expanding updates'}

if __name__=='__main__':
    import sys
    print(json.dumps([evaluate(Path(p)) for p in sys.argv[1:]],indent=2))
