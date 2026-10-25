import sys,json
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from timemap_statistics import observations,walk_forward

def metrics(labels,probs):
 y=np.array(labels);p=np.array(probs);pred=p.argmax(axis=1);conf=p.max(axis=1)
 bins=[]
 for lo in np.arange(0,1,.1):
  mask=(conf>=lo)&(conf<lo+.1 if lo<.9 else conf<=1)
  if mask.any(): bins.append(dict(n=int(mask.sum()),confidence=float(conf[mask].mean()),accuracy=float((pred[mask]==y[mask]).mean())))
 return dict(n=len(y),accuracy=float((pred==y).mean()),balanced_accuracy=float(np.mean([(pred[y==k]==k).mean() for k in range(3) if (y==k).any()])),precision_up=float((y[pred==2]==2).mean()) if (pred==2).any() else None,precision_down=float((y[pred==0]==0).mean()) if (pred==0).any() else None,brier=float(((p-np.eye(3)[y])**2).sum(axis=1).mean()),ece=float(sum(b['n']*abs(b['confidence']-b['accuracy']) for b in bins)/len(y)),calibration_bins=bins)

out={}
for ticker in ['BBCA','ANTM']:
 frame=pd.read_csv(Path(__file__).parent/'inputs'/f'{ticker}.csv');obs=observations(frame,ticker,pd.Timestamp('2026-10-24T03:00Z'))
 obs['label']=np.where(obs.ret>.002,2,np.where(obs.ret<-.002,0,1));ys=[];ps=[];globalps=[]
 for day in sorted(obs.day.unique()):
  train=obs[obs.day<day];test=obs[obs.day==day]
  for row in test.itertuples():
   slot=train[train.slot==row.slot]
   if len(slot)<8:continue
   counts=np.bincount(slot.label,minlength=3);globalcounts=np.bincount(train.label,minlength=3)
   ys.append(row.label);ps.append((counts+1)/(counts.sum()+3));globalps.append((globalcounts+1)/(globalcounts.sum()+3))
 model=metrics(ys,ps);ref,_=walk_forward(obs)
 assert abs(model['accuracy']*100-ref['accuracy_pct'])<1e-9 and abs(model['brier']-ref['brier'])<1e-9
 out[ticker]=dict(period=[str(obs.day.min()),str(obs.day.max())],raw_bars=len(frame),R90_R91=model,expanding_global=metrics(ys,globalps),always_flat=metrics(ys,[[0,1,0]]*len(ys)),uniform_brier=2/3,decision='NO EDGE; unchanged historical estimator; no held-out model promotion')
Path('validation/acceptance/accuracy.json').write_text(json.dumps(out,indent=2))
print(json.dumps({t:{k:v for k,v in r['R90_R91'].items() if k!='calibration_bins'} for t,r in out.items()},indent=2))
