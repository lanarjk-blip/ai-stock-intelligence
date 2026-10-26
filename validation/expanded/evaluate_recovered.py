"""Apply the predeclared experiment to recovered history, without promotion."""
import sys,json,time,hashlib
from pathlib import Path
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from validation.quality import conditional_models as cm
HERE=Path(__file__).parent

def run():
    started=time.perf_counter();now=pd.Timestamp.now(tz='UTC');datasets=[];coverage=[]
    sources={}
    for ticker in ['PTSP','MPXL','BBCA','ANTM','BBRI','TLKM']:
        paths=[ROOT/'validation/acceptance/inputs'/f'{ticker}.csv',ROOT/'validation/reliability/inputs'/f'{ticker}.csv',HERE/'inputs'/f'{ticker}.csv',HERE/'recovered'/f'{ticker}.csv']
        paths=[p for p in paths if p.exists()]
        if not paths:continue
        sources[ticker]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        raw=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True)
        d=cm.features(raw,ticker,now);days=len(d.day.unique())
        coverage.append(dict(ticker=ticker,rows=len(d),days=days))
        if days>=35:datasets.append(d)
    report=dict(coverage=coverage,input_sha256=sources,limitations='Retrospective diagnostic using previously inspected archives; no untouched promotion claim',production_enabled=False,decision='NO PROMOTION; independent prospective evidence required')
    if datasets:
        cm.FEATURES=[f for f in cm.FEATURES if f!='ticker_bbca']
        data=pd.concat(datasets).sort_values(['ts','ticker']);_,validation_days,test_days=cm.split_days(data)
        if '--frozen' in sys.argv:
            frozen=json.loads((HERE/'recovered_results.json').read_text())
            assert frozen['input_sha256']==sources, 'Inputs changed; rerun chronological selection'
            validation=frozen['validation'];selected=frozen['selected_on_validation']
        else:
            vs={name:[] for name in cm.NAMES+['global_baseline','slot_baseline']};ys=[]
            for day in validation_days:
                train=data[data.day<day];test=data[data.day==day];predictions=cm.fit_predict(train,test)
                ys.extend(test.label)
                for name,p in predictions.items():vs[name].extend(p.tolist())
            validation={name:cm.metrics(ys,np.array(p)) for name,p in vs.items()};selected=cm.select(validation)
        train=data[data.day<test_days[0]];test=data[data.day>=test_days[0]];predictions=cm.fit_predict(train,test)
        report.update(selected_on_validation=selected,validation=validation,test={name:cm.metrics(test.label,p) for name,p in predictions.items()},
            training_end=str(train.day.max()),test_dates=[str(d) for d in test_days],
            per_ticker={ticker:{name:cm.metrics(test.loc[test.ticker==ticker,'label'],p[(test.ticker==ticker).to_numpy()]) for name,p in predictions.items()} for ticker in sorted(test.ticker.unique())})
        target=np.eye(3)[test.label.to_numpy()]
        losses={name:((p-target)**2).sum(axis=1) for name,p in predictions.items()}
        gains=pd.DataFrame({'day':test.day.to_numpy(),'gain':losses['global_baseline']-losses[selected]}).groupby('day').gain.mean().to_numpy()
        rng=np.random.default_rng(91)
        bootstrap=rng.choice(gains,size=(2000,len(gains)),replace=True).mean(axis=1)
        report['selected_brier_gain_day_bootstrap95']=np.quantile(bootstrap,[.025,.975]).tolist()
        ledger=[{'ticker':row.ticker,'candle_start':str(row.ts),'feature_asof':str(row.feature_asof),'actual_label':int(row.label),'probabilities':{name:p[i].tolist() for name,p in predictions.items()},'historical_replay':True,'live_forecast':False} for i,row in enumerate(test.itertuples())]
        (HERE/'recovered_replay.json').write_text(json.dumps(ledger,indent=2))
    report['elapsed_seconds']=round(time.perf_counter()-started,3)
    (HERE/'recovered_results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':run()
