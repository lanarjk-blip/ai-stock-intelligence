"""Offline diagnostic only. Never imported by the production app or worker.

Fixed candidates; select by chronological validation Brier, then freeze weights
before a final eight-day test. Previously inspected archives are NOT a novel
promotion holdout. No current target candle prices/volume enter its features.
"""
import json
import hashlib
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from timemap_data import clean
from timemap_statistics import observations

FEATURES=['lag_return','momentum3','volatility5','range_lag','volume_ratio',
          'gap_hours','clock_sin','clock_cos','friday','ticker_bbca']
NAMES=['logistic','random_forest','hist_boost','ensemble']


def features(frame,ticker,cutoff):
    raw=clean(frame,ticker,cutoff)
    eligible=observations(raw,ticker,cutoff)
    local=raw.ts.dt.tz_convert('Asia/Jakarta')
    raw['day']=local.dt.date;raw['slot']=local.dt.strftime('%H:%M')
    d=raw.merge(eligible,on=['day','slot'],how='inner').sort_values('ts').reset_index(drop=True)
    d['lag_return']=d.ret.shift(1)
    d['momentum3']=d.ret.shift(1).rolling(3).sum()
    d['volatility5']=d.ret.shift(1).rolling(5).std()
    d['range_lag']=((d.high-d.low)/d.open).shift(1)
    d['volume_ratio']=d.volume.shift(1)/d.volume.shift(1).rolling(5).mean()
    d['feature_asof']=d.ts.shift(1)+pd.Timedelta(minutes=30)
    d['gap_hours']=(d.ts-d.feature_asof).dt.total_seconds()/3600
    minutes=d.ts.dt.tz_convert('Asia/Jakarta').dt.hour*60+d.ts.dt.minute
    d['clock_sin']=np.sin(2*np.pi*minutes/1440);d['clock_cos']=np.cos(2*np.pi*minutes/1440)
    d['friday']=(d.ts.dt.dayofweek==4).astype(int);d['ticker_bbca']=int(ticker=='BBCA')
    d['ticker']=ticker;d['label']=np.where(d.ret>.002,2,np.where(d.ret<-.002,0,1))
    d=d.dropna(subset=FEATURES)
    assert (d.feature_asof<=d.ts).all()
    return d[['ts','day','slot','ticker','feature_asof','label']+FEATURES]


def models():
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
    return {'logistic':make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=500)),
            'random_forest':RandomForestClassifier(n_estimators=100,max_depth=4,min_samples_leaf=20,random_state=91,n_jobs=1),
            'hist_boost':HistGradientBoostingClassifier(max_iter=60,max_leaf_nodes=7,min_samples_leaf=30,
                         l2_regularization=5,learning_rate=.05,early_stopping=False,random_state=91)}


def fit_predict(train,test):
    assert train.day.max()<test.day.min()
    result={}
    for name,model in models().items():
        model.fit(train[FEATURES],train.label)
        probs=np.zeros((len(test),3));probs[:,model.classes_.astype(int)]=model.predict_proba(test[FEATURES])
        result[name]=probs
    result['ensemble']=np.mean(list(result.values()),axis=0)
    for name in ['slot_baseline','global_baseline']:
        ps=[]
        for row in test.itertuples():
            history=train[train.ticker==row.ticker]
            if name=='slot_baseline':history=history[history.slot==row.slot]
            counts=np.bincount(history.label,minlength=3);ps.append((counts+1)/(counts.sum()+3))
        result[name]=np.array(ps)
    return result


def metrics(y,p):
    y=np.asarray(y);pred=p.argmax(axis=1);confidence=p.max(axis=1)
    recalls=[float((pred[y==k]==k).mean()) if (y==k).any() else None for k in range(3)]
    bins=[]
    for lo in np.arange(0,1,.1):
        mask=(confidence>=lo)&(confidence<lo+.1 if lo<.9 else confidence<=1)
        if mask.any():bins.append(dict(n=int(mask.sum()),confidence=float(confidence[mask].mean()),accuracy=float((pred[mask]==y[mask]).mean())))
    return dict(n=len(y),directional_accuracy=float((pred==y).mean()),
                balanced_accuracy=float(np.mean([v for v in recalls if v is not None])),
                precision_up=float((y[pred==2]==2).mean()) if (pred==2).any() else None,
                precision_down=float((y[pred==0]==0).mean()) if (pred==0).any() else None,
                brier=float(((p-np.eye(3)[y])**2).sum(axis=1).mean()),
                ece=float(sum(b['n']*abs(b['confidence']-b['accuracy']) for b in bins)/len(y)),
                calibration_bins=bins,actual_counts=np.bincount(y,minlength=3).tolist(),
                predicted_counts=np.bincount(pred,minlength=3).tolist())


def split_days(data):
    days=sorted(data.day.unique())
    if len(days)<35:raise ValueError('INSUFFICIENT DAYS: need35 for diagnostic train/validation/test')
    return days[:20],days[20:-8],days[-8:]


def select(validation_metrics):
    # No test metrics are accepted by this selection function.
    return min(NAMES,key=lambda name:(validation_metrics[name]['brier'],name))


def run():
    import sklearn
    started=time.perf_counter()
    data=pd.concat([features(pd.read_csv(ROOT/'validation/acceptance/inputs'/f'{t}.csv'),t,
                    pd.Timestamp('2026-10-24T03:00Z')) for t in ['BBCA','ANTM']],ignore_index=True).sort_values(['ts','ticker'])
    warmup,val_days,test_days=split_days(data)
    ys=[];folds=[];vp={name:[] for name in NAMES+['slot_baseline','global_baseline']}
    for day in val_days:
        train=data[data.day<day];test=data[data.day==day]
        result=fit_predict(train,test);ys.extend(test.label.tolist())
        folds.append(dict(train_end=str(train.day.max()),test_day=str(day),train_n=len(train),test_n=len(test)))
        for name,p in result.items():vp[name].extend(p.tolist())
    validation={name:metrics(ys,np.array(p)) for name,p in vp.items()}
    selected=select(validation)
    train=data[data.day<test_days[0]];test=data[data.day>=test_days[0]]
    predictions=fit_predict(train,test)
    heldout={name:metrics(test.label,p) for name,p in predictions.items()}
    per_ticker={t:{name:metrics(test.loc[test.ticker==t,'label'],p[(test.ticker==t).to_numpy()])
                  for name,p in predictions.items()} for t in sorted(test.ticker.unique())}
    # Paired day-block bootstrap respects shared intraday market conditions.
    y=test.label.to_numpy();target=np.eye(3)[y]
    loss=lambda p:((p-target)**2).sum(axis=1)
    differences=pd.DataFrame({'day':test.day.to_numpy(),'gain':loss(predictions['global_baseline'])-loss(predictions[selected])}).groupby('day').gain.mean().to_numpy()
    rng=np.random.default_rng(91);means=rng.choice(differences,size=(2000,len(differences)),replace=True).mean(axis=1)
    report=dict(diagnostic_only=True,production_enabled=False,decision='NO PROMOTION: previously inspected two-ticker archive; fresh untouched evaluation required',
                sklearn_version=sklearn.__version__,numpy_version=np.__version__,pandas_version=pd.__version__,
                input_sha256={t:hashlib.sha256((ROOT/'validation/acceptance/inputs'/f'{t}.csv').read_bytes()).hexdigest() for t in ['BBCA','ANTM']},
                features=FEATURES,target='open-to-close30m, +/-0.2%; prior completed bars only; no fees/slippage model',
                accepted_rows=len(data),warmup_days=[str(warmup[0]),str(warmup[-1])],
                validation_days=[str(val_days[0]),str(val_days[-1])],test_days=[str(d) for d in test_days],
                selected_on_validation=selected,validation=validation,test=heldout,per_ticker=per_ticker,folds=folds,
                frozen_test_train_end=str(train.day.max()),
                selected_brier_gain_vs_global_day_bootstrap95=np.quantile(means,[.025,.975]).tolist(),elapsed_seconds=round(time.perf_counter()-started,3))
    out=Path(__file__).with_name('conditional_results.json');out.write_text(json.dumps(report,indent=2))
    ledger=[]
    for i,row in enumerate(test.itertuples()):
        ledger.append(dict(ticker=row.ticker,candle_start=str(row.ts),feature_available_at=str(row.feature_asof),
                           training_end=str(train.day.max()),actual_label=int(row.label),
                           historical_replay=True,live_forecast=False,
                           probabilities={name:p[i].tolist() for name,p in predictions.items()}))
    out.with_name('conditional_replay.json').write_text(json.dumps(ledger,indent=2))
    print(json.dumps({k:report[k] for k in ['decision','accepted_rows','selected_on_validation','selected_brier_gain_vs_global_day_bootstrap95','elapsed_seconds']}))
    print(json.dumps({name:{k:v for k,v in m.items() if k not in ['calibration_bins']} for name,m in heldout.items()},indent=2))


if __name__=='__main__':run()
