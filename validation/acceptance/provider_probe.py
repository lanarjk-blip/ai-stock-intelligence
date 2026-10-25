"""Bounded whole-universe probe. Stop on systemic transport/rate-limit failure."""
import sys,json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import timemap_data as data
symbols=json.loads(Path('tests/fixtures/idx_symbols.json').read_text())
ordered=list(dict.fromkeys(['MPXL','BBCA','ANTM','BBRI','GOTO','AALI','ZATA','ZYRX']+symbols))
checkpoint=Path('validation/acceptance/provider_results.json')
previous=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
results=previous.get('results',[]); completed={r['ticker'] for r in results}
ordered=[t for t in ordered if t not in completed]
started=time.time(); now=pd.Timestamp.now(tz='UTC')
def probe(t):
 f=data.load(t,days=5,timeout=2,now=now)
 errors=f.attrs.get('errors',[])
 status='DATA' if not f.empty else 'NO_DATA' if errors and all('EMPTY' in e or 'HTTP 404' in e for e in errors) else 'FETCH_FAILED'
 return dict(checked_at=now.isoformat(),ticker=t,status=status,bars=len(f),latest=f.attrs.get('latest_bar'),data_status=f.attrs.get('data_status'),source=f.attrs.get('source'),errors=errors)
with ThreadPoolExecutor(max_workers=4) as pool:
 for offset in range(0,len(ordered),8):
  batch=list(pool.map(probe,ordered[offset:offset+8]));results+=batch
  Path('validation/acceptance/provider_results.json').write_text(json.dumps({'checked_at':now.isoformat(),'supported':len(symbols),'attempted':len(results),'not_attempted':len(symbols)-len(results),'results':results},indent=2))
  print('PROGRESS',len(results),'data',sum(r['status']=='DATA' for r in results),flush=True)
  if any('HTTP 429' in e for r in batch for e in r['errors']) or all(r['status']=='FETCH_FAILED' for r in batch):
   print('CIRCUIT BREAKER: provider rate limit or systemic transport failure',flush=True);break
print('ELAPSED',round(time.time()-started,2),flush=True)
