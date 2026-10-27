"""Offline comparison of fixed full-day classifiers; never trains on UI refresh."""
import hashlib,json,time,warnings
from pathlib import Path
import numpy as np
import pandas as pd
from timemap_forecast import prepare,schedule
from validation.quality.conditional_models import metrics

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent
PROTOCOL=json.loads((HERE/'protocol.json').read_text())
FEATURES=PROTOCOL['features']


def features(raw,ticker,cutoff):
    d=prepare(raw,ticker,cutoff)
    if d.empty:return pd.DataFrame()
    groups={day:g.sort_values('ts') for day,g in d[d.full].groupby('day')}
    days=sorted(groups);rows=[]
    for i,day in enumerate(days[1:],1):
        previous=groups[days[i-1]];today=groups[day]
        daily_ret=previous.close/previous.open-1
        prior_return=float(previous.iloc[-1].close/previous.iloc[0].open-1)*100
        prior_vol=float(np.sqrt(np.mean(np.square(daily_ret))))*100
        for clock in PROTOCOL['issue_times_wib']:
            issue=pd.Timestamp(str(day)+' '+clock,tz='Asia/Jakarta')
            # Archive replay assumes each completed bar is available at its end.
            prefix=today[today.ts+pd.Timedelta(minutes=30)<=issue.tz_convert('UTC')]
            opening=today[today.slot.eq('09:00')&(today.ts<=issue.tz_convert('UTC'))] if clock!='08:50' else today.iloc[:0]
            asof=max([previous.ts.max()+pd.Timedelta(minutes=30)]+
                      ([prefix.ts.max()+pd.Timedelta(minutes=30)] if len(prefix) else [])+
                      ([opening.ts.min()] if len(opening) else []))
            for target in today.itertuples():
                start=target.ts.tz_convert('Asia/Jakarta')
                if start<issue:continue
                tod=start.hour*60+start.minute
                row={'ticker':ticker,'day':str(day),'issue':clock,'slot':target.slot,
                     'generated_at':issue.isoformat(),'feature_asof':asof.isoformat(),
                     'label':2 if target.ret>.002 else 0 if target.ret < -.002 else 1,
                     'prior_return':prior_return,'prior_volatility':prior_vol,
                     'opening_gap':float(opening.iloc[0].open/previous.iloc[-1].close-1)*100 if len(opening) else 0.,
                     'opening_known':int(len(opening)>0),
                     'prefix_return':float(prefix.iloc[-1].close/prefix.iloc[0].open-1)*100 if len(prefix) else 0.,
                     'prefix_volume':float(prefix.volume.mean()/previous.volume.mean()) if len(prefix) else 0.,
                     'prefix_known':int(len(prefix)>0),'prefix_bars':len(prefix),
                     'issue_clock':(issue.hour*60+issue.minute)/1440,
                     'target_sin':np.sin(2*np.pi*tod/1440),'target_cos':np.cos(2*np.pi*tod/1440),
                     'horizon_hours':(start-issue).total_seconds()/3600,'friday':int(start.dayofweek==4)}
                rows.append(row)
    result=pd.DataFrame(rows)
    if len(result):
        result[FEATURES]=result[FEATURES].clip(-50,50)
        assert (pd.to_datetime(result.feature_asof,utc=True)<=pd.to_datetime(result.generated_at,utc=True)).all()
    return result


def load():
    frames=[];coverage=[];hashes={};cutoff=pd.Timestamp.now(tz='UTC')
    for ticker in PROTOCOL['tickers']:
        folders=['validation/acceptance/inputs','validation/reliability/inputs','validation/expanded/inputs',
                 'validation/expanded/recovered','validation/engine_upgrade','validation/broad_tickers/inputs']
        paths=[ROOT/d/f'{ticker}.csv' for d in folders if (ROOT/d/f'{ticker}.csv').exists()]
        hashes[ticker]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        if not paths:continue
        raw=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True);raw.attrs={'ticker':ticker}
        prepared=prepare(raw,ticker,cutoff)
        days=prepared.day.nunique() if len(prepared) else 0
        enough=days>=PROTOCOL['minimum_coverage_days']
        coverage.append({'ticker':ticker,'days':int(days),'included':enough})
        if enough:frames.append(features(raw,ticker,cutoff))
    return pd.concat(frames,ignore_index=True).sort_values(['day','ticker','issue','slot']),coverage,hashes


def models():
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from xgboost import XGBClassifier
    config=PROTOCOL['models'];mlp=dict(config['mlp']);mlp['hidden_layer_sizes']=tuple(mlp['hidden_layer_sizes'])
    return {'logistic':make_pipeline(StandardScaler(),LogisticRegression(**config['logistic'])),
            'xgboost':XGBClassifier(**config['xgboost']),
            'mlp':make_pipeline(StandardScaler(),MLPClassifier(**mlp))}


def probabilities(model,x):
    p=np.zeros((len(x),3));p[:,np.asarray(model.classes_,dtype=int)]=model.predict_proba(x)
    if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1):raise ValueError('Bad probabilities')
    return p


def fit_predict(train,test):
    if train.day.max()>=test.day.min():raise ValueError('Date leakage')
    fitted=models();result={};notices=[]
    for name,model in fitted.items():
        with warnings.catch_warnings(record=True) as found:
            warnings.simplefilter('always');model.fit(train[FEATURES],train.label)
        notices.extend({'model':name,'warning':str(x.message)} for x in found)
        result[name]=probabilities(model,test[FEATURES])
    result['ensemble']=np.mean(list(result.values()),axis=0)
    return fitted,result,notices


def variants(predictions):
    return {name+f'@{alpha}':alpha*p+(1-alpha)/3 for name,p in predictions.items() for alpha in [.25,.5,1.]}


def choose(scores):
    return min(scores,key=lambda name:(scores[name]['brier'],name))


def freeze(fitted,selected,train):
    name,alpha=selected.split('@');names=list(fitted) if name=='ensemble' else [name]
    checksums={}
    for name in names:
        model=fitted[name];path=HERE/f'frozen_{name}.json'
        if name=='xgboost':model.save_model(path)
        else:
            scaler,estimator=model.steps[0][1],model.steps[-1][1]
            state={'type':name,'features':FEATURES,'classes':estimator.classes_.tolist(),
                   'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist()}
            if name=='logistic':state.update(coef=estimator.coef_.tolist(),intercept=estimator.intercept_.tolist())
            else:state.update(coefs=[x.tolist() for x in estimator.coefs_],intercepts=[x.tolist() for x in estimator.intercepts_])
            path.write_text(json.dumps(state,indent=2))
        checksums[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'selected_on_validation':selected,'uniform_mix_alpha':float(alpha),'training_end':str(train.day.max()),
              'features':FEATURES,'class_order':['DOWN','DATAR','UP'],'components':checksums,
              'protocol_sha256':hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
              'feature_engine_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'frozen_at':pd.Timestamp.now(tz='UTC').isoformat(),'production_enabled':False,
              'retrospective_test':True,'independent_prospective_evidence':False}
    (HERE/'frozen.json').write_text(json.dumps(manifest,indent=2));return manifest


def run():
    start=time.perf_counter();data,coverage,hashes=load()
    test_dates=PROTOCOL['test_dates'];before=sorted(set(data.day)-set(test_dates))
    before=[d for d in before if d<test_dates[0]]
    val_dates=before[-6:];warmup=before[:-6]
    if len(warmup)<PROTOCOL['minimum_training_dates']:raise ValueError('Insufficient warmup dates')
    all_probs={};actual=[];folds=[];notices=[]
    for day in val_dates:
        train=data[data.day<day];test=data[data.day.eq(day)]
        _,pred,warn=fit_predict(train,test);notices+=warn;actual.extend(test.label.tolist())
        for name,p in variants(pred).items():all_probs.setdefault(name,[]).extend(p.tolist())
        folds.append({'training_end':str(train.day.max()),'validation_date':day,'train_rows':len(train),'test_rows':len(test)})
    validation={name:metrics(actual,np.asarray(p)) for name,p in all_probs.items()}
    selected=choose(validation)
    train=data[data.day<test_dates[0]];test=data[data.day.isin(test_dates)]
    old=json.loads((ROOT/'validation/day_forecast/replay.json').read_text())
    comparison={(r['ticker'],r['day'],r['issue'],r['slot']):r for r in old}
    test=test[[key in comparison for key in zip(test.ticker,test.day,test.issue,test.slot)]]
    fitted,pred,warn=fit_predict(train,test);notices+=warn
    frozen=freeze(fitted,selected,train) # before computing final metrics
    predictions=variants(pred)
    identities=[(r.ticker,r.day,r.issue,r.slot) for r in test.itertuples()]
    if set(identities)!=set(comparison):raise ValueError('Final comparison outcome set differs')
    baseline=np.asarray([comparison[key]['baseline'] for key in identities])
    old_p=np.asarray([comparison[key]['p'] for key in identities])
    predictions.update(R90=baseline,uniform=np.full((len(test),3),1/3),previous_analogue=old_p)
    scores={name:metrics(test.label,p) for name,p in predictions.items()}
    p=predictions[selected];truth=np.eye(3)[test.label.to_numpy()]
    intervals={}
    for name in ['R90','uniform','previous_analogue']:
        gain=((predictions[name]-truth)**2).sum(axis=1)-((p-truth)**2).sum(axis=1)
        daily=pd.DataFrame({'day':test.day.to_numpy(),'gain':gain}).groupby('day').gain.mean().to_numpy()
        means=np.random.default_rng(92).choice(daily,size=(2000,len(daily)),replace=True).mean(axis=1)
        intervals[name]=np.quantile(means,[.025,.975]).tolist()
    by_ticker={}
    for ticker in sorted(test.ticker.unique()):
        mask=test.ticker.eq(ticker).to_numpy()
        by_ticker[ticker]={name:metrics(test.loc[mask,'label'],values[mask]) for name,values in [(selected,p),('R90',baseline)]}
    report={'checked_at':pd.Timestamp.now(tz='UTC').isoformat(),'selected_on_validation':selected,
            'validation_dates':val_dates,'validation':validation,'folds':folds,'test_dates':test_dates,
            'training_rows':len(train),'forecast_instances':len(test),
            'unique_outcomes':len(set(zip(test.ticker,test.day,test.slot))),
            'coverage':coverage,'input_sha256':hashes,'final_metrics':scores,'day_block_brier_gain_95pct':intervals,
            'by_ticker':by_ticker,'frozen':frozen,'warnings':notices,
            'promotion':'BLOCKED: previously inspected archive; no independent prospective/cost evidence',
            'elapsed_seconds':round(time.perf_counter()-start,3)}
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    replay=[]
    for i,row in enumerate(test.itertuples()):
        replay.append({'ticker':row.ticker,'day':row.day,'issue':row.issue,'slot':row.slot,
                       'feature_asof':row.feature_asof,'generated_at':row.generated_at,'training_end':frozen['training_end'],
                       'actual':int(row.label),'features':[float(getattr(row,f)) for f in FEATURES],
                       'p':p[i].tolist(),'R90':baseline[i].tolist(),'previous_analogue':old_p[i].tolist()})
    (HERE/'replay.json').write_text(json.dumps(replay,separators=(',',':')))
    print(json.dumps({'selected':selected,'training_rows':len(train),'forecasts':len(test),'metrics':{k:scores[k] for k in [selected,'R90','uniform','previous_analogue']},'intervals':intervals,'elapsed_seconds':report['elapsed_seconds']}))
    return report

if __name__=='__main__':run()
