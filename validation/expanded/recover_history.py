"""Recover archived training evidence in bounded seven-day requests.

Offline only: never increases production refresh cost. Original snapshot retained.
"""
import sys,json,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import timemap_data
HERE=Path(__file__).parent

def run():
    now=pd.Timestamp.now(tz='UTC');out=HERE/'recovered';out.mkdir(exist_ok=True)
    # Fixed focused recovery after documented long-request timeouts.
    symbols=['PTSP','MPXL','BBCA','ANTM'];manifest=[]
    def fetch(ticker):
        chunks=[];attempts=[]
        for offset in range(0,56,7):
            end=now-pd.Timedelta(days=offset)
            f,source,errors=timemap_data._download(ticker,7,3,end)
            attempts.append({'cutoff':str(end),'source':source,'bars':len(f),'errors':errors})
            if not f.empty:chunks.append(f)
            if any('HTTP 429' in e for e in errors):break
        result={'ticker':ticker,'attempts':attempts,'bars':0}
        if chunks:
            frame=timemap_data.clean(pd.concat(chunks,ignore_index=True),ticker,now)
            path=out/f'{ticker}.csv';frame.to_csv(path,index=False)
            result.update(bars=len(frame),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(fetch,symbols):
            manifest.append(r);(out/'manifest.json').write_text(json.dumps(manifest,indent=2));print((r['ticker'],r['bars']),flush=True)
if __name__=='__main__':run()
