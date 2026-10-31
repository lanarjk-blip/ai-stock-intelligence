import json
import os
import runpy
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from market_session import session_status
from runtime_profile import timemap_only
from timemap_data import bar_freshness
from timemap_forecast import predict
from timemap_forecast_ui import connect as forecast_connect, record, saved
from timemap_runtime import connect, due_tickers, mark_refresh, persist_verified
from timemap_worker import run_once
from test_day_forecast import fixture

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ('app','hold_engine','dynamic_engine','premarket_intelligence',
          'premarket_scheduler','premarket_preliminary','accuracy_guardrails','xgboost')


def watch(path,tickers,now):
    connect(path).close()
    con=forecast_connect(path)
    con.executemany('INSERT OR REPLACE INTO timemap_forecast_watch VALUES(?,?)',
                    [(t,now.isoformat()) for t in tickers])
    con.commit();con.close()


def tape(ticker,now):
    d=fixture(ticker)
    d=d[pd.to_datetime(d.ts,utc=True)<=now.tz_convert('UTC')].copy()
    d.attrs={'ticker':ticker,'source':'TEST FIXTURE','fetched_at':now.isoformat()}
    return d


@pytest.mark.parametrize('cfg',[{}, {'runtime_workload':'timemap'}, {'runtime_workload':'typo'}])
def test_default_and_invalid_profile_do_not_enable_legacy(cfg):
    assert timemap_only(cfg)
    assert not timemap_only({'runtime_workload':'legacy'})


def test_real_entrypoint_dispatches_without_importing_legacy_scanner():
    script='''
import runpy,sys,types
m=types.ModuleType('timemap_worker')
def run():
    print('FOCUSED_DISPATCH')
    print([name for name in %r if name in sys.modules])
m.run_forever=run
sys.modules['timemap_worker']=m
runpy.run_path('worker_base.py',run_name='__main__')
''' % (LEGACY,)
    env={k:v for k,v in os.environ.items() if k not in {'DATABASE_URL','NEON_DATABASE_URL','ASI_RUNTIME_API_URL'}}
    env['AI_ENGINE_WORKER']='1'
    result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'FOCUSED_DISPATCH\n[]' in result.stdout


def test_startup_keeps_watchdog_and_heartbeat_without_legacy_threads(monkeypatch):
    import persistent_bridge as pb
    import local_runtime_health as health
    import requests
    names=[]
    monkeypatch.setenv('AI_ENGINE_WORKER','1')
    monkeypatch.setattr(sys,'argv',['worker_base.py'])
    monkeypatch.setitem(sys.modules,'runtime_supervisor',SimpleNamespace(CLAIM={}))
    monkeypatch.setattr(health,'write_worker_heartbeat',lambda **kw:None)
    monkeypatch.setattr(pb,'postgres_enabled',lambda:False)
    monkeypatch.setattr(pb,'enabled',lambda:False)
    monkeypatch.setattr(requests,'get',requests.get) # restore sitecustomize's wrapper afterwards
    class Thread:
        def __init__(self,**kw):names.append(kw['name'])
        def start(self):pass
    monkeypatch.setattr(threading,'Thread',Thread)
    source=ROOT/'sitecustomize.py'
    exec(compile(source.read_text(),str(source),'exec'),{'__file__':str(source),'__name__':'sitecustomize_test'})
    assert set(names)=={'asi-runtime-watchdog','asi-local-heartbeat'}


def test_focused_ui_still_blocks_duplicate_external_worker_without_legacy_replica(monkeypatch):
    import persistent_bridge as pb
    import external_runtime_guard
    import requests
    calls=[]
    monkeypatch.delenv('AI_ENGINE_WORKER',raising=False)
    monkeypatch.setattr(sys,'argv',['streamlit'])
    monkeypatch.setattr(pb,'enabled',lambda:True)
    monkeypatch.setattr(pb,'api_enabled',lambda:True)
    monkeypatch.setattr(requests,'get',requests.get)
    monkeypatch.setattr(external_runtime_guard,'install',lambda:calls.append('guard'))
    monkeypatch.setitem(sys.modules,'replica_runtime',SimpleNamespace(
        pull_ui_once=lambda:pytest.fail('Legacy replica must stay idle'),
        start_ui_pull=lambda:pytest.fail('Legacy replica must stay idle')))
    source=ROOT/'sitecustomize.py'
    exec(compile(source.read_text(),str(source),'exec'),{'__file__':str(source),'__name__':'sitecustomize_test'})
    assert calls==['guard']


def test_no_watchlist_means_no_market_requests_or_scan(tmp_path):
    def forbidden(*a,**kw):raise AssertionError('Unexpected provider request')
    path=tmp_path/'m.db'
    result=run_once(path,pd.Timestamp('2026-10-27T10:00:00+07:00'),loader=forbidden)
    assert result=={'mode':'timemap','checked':0,'statuses':{},'prepared':0,'new_outcomes':0}
    con=sqlite3.connect(path);state=dict(con.execute('SELECT key,value FROM state'));con.close()
    assert state['data_quality_status']=='BLOCKED'


def test_bounded_queue_checks_every_watched_ticker_without_starvation(tmp_path):
    path=tmp_path/'m.db';now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    tickers=['AAA','BBB','CCC','DDD','EEE','FFF','GGG','HHH','III']
    watch(path,tickers,now)
    calls=[]
    def loader(ticker,**kw):
        calls.append(ticker)
        raise TimeoutError('Provider fixture unavailable')
    batches=[run_once(path,now+pd.Timedelta(seconds=i),loader=loader) for i in range(3)]
    assert [x['checked'] for x in batches]==[4,4,1]
    assert sorted(calls)==tickers
    assert all('DATA UNAVAILABLE' in s for b in batches for s in b['statuses'].values())
    assert not saved(path,'AAA',now.date().isoformat())


@pytest.mark.parametrize('before,after',[
 ('2026-10-23T15:00+07:00','2026-10-26T09:00+07:00'),
 ('2026-10-26T15:00+07:00','2026-10-27T09:00+07:00'),
 ('2026-10-27T11:55+07:00','2026-10-27T13:30+07:00'),
 ('2026-10-30T15:00+07:00','2026-11-02T09:00+07:00'),
 ('2026-12-30T15:00+07:00','2027-01-04T09:00+07:00'),
])
def test_day_and_session_changes_force_new_check(tmp_path,before,after):
    path=tmp_path/'m.db';old=pd.Timestamp(before);new=pd.Timestamp(after)
    watch(path,['BBCA'],new)
    mark_refresh(path,'BBCA',session_status(now=old),old,'DELAYED')
    assert due_tickers(path,new,session_status(now=new))==['BBCA']
    # Even an implausibly recent attempt cannot bypass a changed session/date.
    mark_refresh(path,'BBCA',session_status(now=old),new,'DELAYED')
    assert due_tickers(path,new,session_status(now=new))==['BBCA']


def test_stored_yesterday_remains_yesterday_after_reopen_and_failed_provider(tmp_path):
    path=tmp_path/'m.db';old=pd.Timestamp('2026-10-26T15:31:00+07:00');new=pd.Timestamp('2026-10-27T09:00:00+07:00')
    d=tape('BBCA',old);persist_verified(path,'BBCA',d,old)
    con=sqlite3.connect(path);before=con.execute('SELECT * FROM intraday ORDER BY ts').fetchall();con.close()
    stale=d.copy();stale.attrs=dict(d.attrs,fallback=True,source='LAST GOOD CACHE')
    assert persist_verified(path,'BBCA',stale,new)==0
    assert bar_freshness(stale,new)=='WAITING FOR NEW BAR'
    con=sqlite3.connect(path);assert con.execute('SELECT * FROM intraday ORDER BY ts').fetchall()==before;con.close()
    with pytest.raises(ValueError,match='TICKER MISMATCH'):
        persist_verified(path,'MPXL',d,new)


def test_slow_response_cannot_overwrite_newer_observation(tmp_path):
    path=tmp_path/'m.db';now=pd.Timestamp('2026-10-27T09:40+07:00')
    d=tape('BBCA',now);persist_verified(path,'BBCA',d,now)
    slow=d.copy();slow.loc[slow.index[-1],'close']=slow.iloc[-1].low
    slow.attrs=dict(d.attrs,fetched_at=(now-pd.Timedelta(minutes=5)).isoformat())
    persist_verified(path,'BBCA',slow,now)
    con=sqlite3.connect(path)
    close,stamp=con.execute('SELECT close,updated_at FROM intraday ORDER BY ts DESC LIMIT 1').fetchone();con.close()
    assert close==d.iloc[-1].close and pd.Timestamp(stamp)==now


def test_worker_settles_original_predictions_without_old_engines(tmp_path):
    path=tmp_path/'m.db';issued=pd.Timestamp('2026-10-27T08:50+07:00');now=pd.Timestamp('2026-10-27T10:01+07:00')
    watch(path,['BBCA'],issued)
    record(path,predict(fixture(),'BBCA',issued))
    result=run_once(path,now,loader=lambda ticker,now:tape(ticker,now))
    assert result['checked']==1 and result['new_outcomes']==2
    records=saved(path,'BBCA',now.date().isoformat())
    assert {r['generated_at'] for r in records}=={issued.isoformat()}
    assert sum(r['actual_label'] is not None for r in records)==2
    # Immediate restart checks retry state and does not score twice.
    again=run_once(path,now,loader=lambda *a,**kw:pytest.fail('Duplicate fetch'))
    assert again['checked']==0 and again['new_outcomes']==0


def test_premarket_time_map_plans_continue_without_legacy_premarket_feature(tmp_path):
    path=tmp_path/'m.db'
    monday=pd.Timestamp('2026-10-26T08:50+07:00');tuesday=pd.Timestamp('2026-10-27T08:50+07:00')
    watch(path,['BBCA'],monday)
    loader=lambda ticker,now:tape(ticker,now)
    assert run_once(path,monday,loader=loader)['prepared']==10
    originals=saved(path,'BBCA','2026-10-26')
    assert run_once(path,tuesday,loader=loader)['prepared']==10
    todays=saved(path,'BBCA','2026-10-27')
    assert len(originals)==len(todays)==10
    assert {r['generated_at'] for r in todays}=={tuesday.isoformat()}
    assert {r['generated_at'] for r in saved(path,'BBCA','2026-10-26')}=={monday.isoformat()}
    assert all(pd.Timestamp(r['latest_candle']).date()<tuesday.date() for r in todays)
    assert all(r['actual_label'] is None for r in todays)


@pytest.mark.parametrize('time',['2026-10-31T10:00+07:00','2026-12-25T10:00+07:00',
                                  '2026-10-27T02:00+07:00','2026-10-27T18:00+07:00'])
def test_closed_windows_do_not_spend_provider_requests(tmp_path,time):
    now=pd.Timestamp(time);path=tmp_path/'m.db';watch(path,['BBCA'],now)
    result=run_once(path,now,loader=lambda *a,**kw:pytest.fail('Off-window fetch'))
    assert result['checked']==0


def test_checkpoint_restore_preserves_forecasts_without_daily_scanner_data(tmp_path,monkeypatch):
    import persistent_bridge as pb
    path=tmp_path/'m.db';now=pd.Timestamp('2026-10-27T08:50+07:00')
    record(path,predict(fixture(),'BBCA',now))
    monkeypatch.setattr(pb,'LOCAL_DB',str(path))
    monkeypatch.setattr(pb,'postgres_enabled',lambda:True)
    monkeypatch.setattr(pb,'_pg_connect',lambda:pytest.fail('Must not restore over local ledger'))
    assert pb.restore_worker_checkpoint_if_needed()['status']=='LOCAL_WARM'
    assert len(saved(path,'BBCA',now.date().isoformat()))==10


def test_forecasts_are_checkpointed_even_without_legacy_daily_prices(tmp_path,monkeypatch):
    import gzip
    import hashlib
    import persistent_bridge as pb
    from test_persistence_mirror_r24 import _FakeConnection
    path=tmp_path/'m.db';now=pd.Timestamp('2026-10-27T08:50+07:00')
    record(path,predict(fixture(),'BBCA',now))
    fake=_FakeConnection()
    monkeypatch.setattr(pb,'LOCAL_DB',str(path))
    monkeypatch.setattr(pb,'DB_DIR',str(tmp_path))
    monkeypatch.setattr(pb,'postgres_enabled',lambda:True)
    monkeypatch.setattr(pb,'ensure_remote_schema',lambda:None)
    monkeypatch.setattr(pb,'_pg_connect',lambda:fake)
    pb.sync_worker_checkpoint(force=True)
    payload=next(args for sql,args in fake.cur.calls if 'INSERT INTO asi_worker_checkpoint' in sql)
    raw=gzip.decompress(payload[0])
    assert hashlib.sha256(raw).hexdigest()==payload[1]
    backup=tmp_path/'verified.db';backup.write_bytes(raw)
    assert len(saved(backup,'BBCA',now.date().isoformat()))==10


def test_focused_bridge_does_not_touch_old_opportunity_or_premarket_records(tmp_path,monkeypatch):
    import persistent_bridge as pb
    from test_persistence_mirror_r24 import _FakeConnection
    path=tmp_path/'m.db';con=connect(path)
    con.execute("INSERT INTO state VALUES('runtime_workload','timemap')")
    con.execute("INSERT INTO state VALUES('premarket_old','keep')")
    con.commit();con.close()
    fake=_FakeConnection()
    monkeypatch.setattr(pb,'LOCAL_DB',str(path))
    monkeypatch.setattr(pb,'postgres_enabled',lambda:True)
    monkeypatch.setattr(pb,'ensure_remote_schema',lambda:None)
    monkeypatch.setattr(pb,'_pg_connect',lambda:fake)
    result=pb.sync_timemap_runtime({'pid':123,'generation':'test'})
    assert result['state']==1 and fake.committed
    assert all('opportunity' not in sql and 'premarket' not in sql and 'DELETE' not in sql for sql,_ in fake.cur.calls)


def test_legacy_hold_query_uses_database_connection_and_ticker_parameters(tmp_path,monkeypatch):
    import warnings
    import hold_engine
    path=tmp_path/'hold.db';con=sqlite3.connect(path)
    con.execute('CREATE TABLE prices(ticker TEXT,date TEXT,close REAL,volume REAL)')
    for i,day in enumerate(pd.date_range('2026-08-01',periods=60)):
        con.executemany('INSERT INTO prices VALUES(?,?,?,?)',[
            ('UPTEST',day.date().isoformat(),100+i,1000),
            ('DNTEST',day.date().isoformat(),200-i,1000)])
    con.commit();con.close();connections=[]
    def dbcon():
        con=sqlite3.connect(path);connections.append(con);return con
    monkeypatch.setattr(hold_engine,'dbcon',dbcon)
    with warnings.catch_warnings():
        warnings.simplefilter('error',UserWarning)
        up=hold_engine._daily_context('UPTEST');down=hold_engine._daily_context('DNTEST')
        invalid=hold_engine._daily_context("UPTEST' OR 1=1 --")
    assert up['trend']=='BULLISH' and up['ret5']>0
    assert down['trend']=='BEARISH' and down['ret5']<0
    assert invalid['trend']=='UNKNOWN'
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError):con.execute('SELECT 1')


def test_legacy_hold_failed_query_also_closes_connection(tmp_path,monkeypatch):
    import hold_engine
    con=sqlite3.connect(tmp_path/'missing.db')
    monkeypatch.setattr(hold_engine,'dbcon',lambda:con)
    assert hold_engine._daily_context('BBCA')['trend']=='UNKNOWN'
    with pytest.raises(sqlite3.ProgrammingError):con.execute('SELECT 1')
