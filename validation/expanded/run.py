"""One bounded collection and chronological benchmark, never a production import."""
import json,sys,hashlib,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import timemap_data
from validation.quality import conditional_models as cm
HERE=Path(__file__).parent

def run():
    protocol=json.loads((HERE/'protocol.json').read_text());now=pd.Timestamp.now(tz='UTC');started=time.perf_counter()
    manifest=[]
    def fetch(ticker):
        f=timemap_data.load(ticker,days=59,timeout=3,now=now)
        r={'ticker':ticker,'checked_at':str(now),'bars':len(f),'metadata':f.attrs}
        if not f.empty:
            path=HERE/'inputs'/f'{ticker}.csv';f.to_csv(path,index=False);r['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        return r
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i in range(0,len(protocol['tickers']),2):
            results=list(pool.map(fetch,protocol['tickers'][i:i+2]));manifest.extend(results)
            (HERE/'manifest.json').write_text(json.dumps(manifest,indent=2))
            print([(x['ticker'],x['bars']) for x in results],flush=True)
            if any('HTTP 429' in error for r in results for error in r['metadata'].get('errors',[])):break
    datasets=[];coverage=[]
    for r in manifest:
        if not r['bars']:coverage.append({'ticker':r['ticker'],'status':'FETCH FAILED / NO DATA'});continue
        d=cm.features(pd.read_csv(HERE/'inputs'/f"{r['ticker']}.csv"),r['ticker'],now)
        days=len(d.day.unique());coverage.append({'ticker':r['ticker'],'eligible_feature_rows':len(d),'days':days,'status':'EVALUABLE' if days>=35 else 'INSUFFICIENT HISTORY'})
        if days>=35:datasets.append(d)
    report={'protocol':protocol,'coverage':coverage,'checked_at':str(now),'production_enabled':False,'decision':'NO PROMOTION','elapsed_seconds':time.perf_counter()-started}
    if datasets:
        data=pd.concat(datasets).sort_values(['ts','ticker'])
        # Remove the old two-ticker identity feature for a shared multi-ticker model.
        cm.FEATURES=[x for x in cm.FEATURES if x!='ticker_bbca']
        _,val_days,test_days=cm.split_days(data)
        vp={n:[] for n in cm.NAMES+['global_baseline','slot_baseline']};ys=[]
        for day in val_days:
            train=data[data.day<day];test=data[data.day==day]
            if train.label.nunique()<2:continue
            ps=cm.fit_predict(train,test);ys.extend(test.label)
            for n,p in ps.items():vp[n].extend(p.tolist())
        validation={n:cm.metrics(ys,np.array(p)) for n,p in vp.items()}
        selected=cm.select(validation)
        train=data[data.day<test_days[0]];test=data[data.day>=test_days[0]];ps=cm.fit_predict(train,test)
        report.update(selected_on_validation=selected,validation=validation,test={n:cm.metrics(test.label,p) for n,p in ps.items()},
            total_feature_rows=len(data),test_dates=[str(d) for d in test_days],train_end=str(train.day.max()),
            per_ticker={t:{n:cm.metrics(test.loc[test.ticker==t,'label'],p[(test.ticker==t).to_numpy()]) for n,p in ps.items()} for t in sorted(test.ticker.unique())})
    report['elapsed_seconds']=round(time.perf_counter()-started,3)
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ['coverage','decision','elapsed_seconds']},indent=2))

if __name__=='__main__':run()
