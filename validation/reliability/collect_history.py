"""Bounded public-data snapshot collection; never trains or promotes a model."""
import sys,json,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import timemap_data
import timemap_statistics
SYMBOLS=['MPXL','BMRI','BBRI','TLKM','GOTO','BRIS','PTBA','JPFA']
def run():
 out=Path(__file__).parent;inputs=out/'inputs';inputs.mkdir(exist_ok=True)
 now=pd.Timestamp.now(tz='UTC');results=[]
 def fetch(symbol):
  f=timemap_data.load(symbol,days=59,timeout=6,now=now)
  r=dict(ticker=symbol,checked_at=now.isoformat(),bars=len(f),metadata=f.attrs)
  if not f.empty:
   path=inputs/(symbol+'.csv');f.to_csv(path,index=False);r['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
   obs=timemap_statistics.observations(f,symbol,now)
   r['eligible_bars']=len(obs);r['validation']=timemap_statistics.walk_forward(obs)[0]
  return r
 with ThreadPoolExecutor(max_workers=2) as pool:
  for start in range(0,len(SYMBOLS),2):
   batch=list(pool.map(fetch,SYMBOLS[start:start+2]));results.extend(batch)
   (out/'history_manifest.json').write_text(json.dumps({'requested':SYMBOLS,'attempted':len(results),'results':results},indent=2))
   print([(r['ticker'],r['bars']) for r in batch],flush=True)
   if any('HTTP 429' in e for r in batch for e in r['metadata']['errors']):break
 return results
if __name__=='__main__':run()
