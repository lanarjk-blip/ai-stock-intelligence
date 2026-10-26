"""Explicit bounded collection modes; never run by production or on import."""
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from timemap_data import load, clean, _download

HERE = Path(__file__).parent


def recover():
    protocol=json.loads((HERE/'protocol.json').read_text())
    cfg=protocol['bounded_weekly_recovery'];now=pd.Timestamp.now(tz='UTC')
    output=HERE/'recovered';output.mkdir(exist_ok=True)
    stop=Event();records=[];started=time.perf_counter()
    def ticker(t):
        chunks=[];attempts=[]
        for i in range(cfg['windows']):
            if stop.is_set():break
            end=now-pd.Timedelta(days=i*cfg['days_per_window'])
            frame,source,errors=_download(t,cfg['days_per_window'],cfg['timeout_seconds'],end)
            attempts.append(dict(cutoff=str(end),rows=len(frame),source=source,errors=errors))
            if not frame.empty:chunks.append(frame)
            if any('HTTP 429' in e for e in errors):stop.set()
        record=dict(ticker=t,attempts=attempts,rows=0)
        if chunks:
            frame=clean(pd.concat(chunks,ignore_index=True),t,now)
            path=output/f'{t}.csv';frame.to_csv(path,index=False)
            record.update(rows=len(frame),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return record
    with ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
        for record in pool.map(ticker,cfg['tickers']):
            records.append(record)
            (output/'manifest.json').write_text(json.dumps(dict(checked_at=now.isoformat(),results=records,rate_limit_stop=stop.is_set(),elapsed_seconds=round(time.perf_counter()-started,3)),indent=2))
            print((record['ticker'],record['rows']),flush=True)


if __name__=='__main__':
    if sys.argv[1:] != ['--recover']:
        raise SystemExit('Use --recover explicitly; no implicit universe scan')
    recover()
