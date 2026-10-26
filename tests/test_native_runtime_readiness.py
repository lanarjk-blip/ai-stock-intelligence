"""Acceptance of native runtime gates without relying on startup UI patches."""
import sqlite3
import sys
from types import SimpleNamespace
from datetime import datetime,timezone
import pytest


def quality_db(tmp_path,monkeypatch,status='READY'):
    import dynamic_opportunity_ui as ui
    (tmp_path/'db').mkdir()
    db=tmp_path/'db'/'market.db'
    con=sqlite3.connect(db);con.execute('CREATE TABLE state(key TEXT,value TEXT)')
    con.executemany('INSERT INTO state VALUES(?,?)',[
        ('data_quality_status',status),('data_quality_reasons','original reason'),
        ('data_quality_ready_ratio','0.95')])
    con.commit();con.close()
    monkeypatch.setattr(ui,'__file__',str(tmp_path/'dynamic_opportunity_ui.py'))
    return ui


@pytest.mark.parametrize('status',['LOCAL_RUNTIME_MISSING','LOCAL_RUNTIME_STALE','LOCAL_BUILD_MISMATCH','RUNTIME_ENGINE_STALLED'])
def test_persisted_ready_cannot_override_unhealthy_worker(tmp_path,monkeypatch,status):
    ui=quality_db(tmp_path,monkeypatch)
    import runtime_health
    monkeypatch.setattr(runtime_health,'remote_runtime_health',lambda:{'required':True,'ready':False,'status':status,'reason':'heartbeat invalid'})
    result=ui._quality_gate_state()
    assert result['known'] and not result['ready'] and result['status']==status
    assert 'original reason' in result['reasons'] and 'heartbeat invalid' in result['reasons']
    assert result['ready_ratio']=='0.95'


@pytest.mark.parametrize('response',[None,{},'READY'])
def test_malformed_runtime_response_fails_closed(tmp_path,monkeypatch,response):
    ui=quality_db(tmp_path,monkeypatch)
    import runtime_health
    monkeypatch.setattr(runtime_health,'remote_runtime_health',lambda:response)
    result=ui._quality_gate_state()
    assert not result['ready'] and result['status']=='RUNTIME_UNAVAILABLE'


def test_healthy_runtime_does_not_upgrade_bad_data(tmp_path,monkeypatch):
    ui=quality_db(tmp_path,monkeypatch,'DATA_NOT_READY')
    import runtime_health
    monkeypatch.setattr(runtime_health,'remote_runtime_health',lambda:{'required':True,'ready':True,'status':'RUNTIME_READY'})
    assert not ui._quality_gate_state()['ready']


def test_ready_data_with_healthy_worker_is_preserved(tmp_path,monkeypatch):
    ui=quality_db(tmp_path,monkeypatch)
    import runtime_health
    monkeypatch.setattr(runtime_health,'remote_runtime_health',lambda:{'required':True,'ready':True,'status':'RUNTIME_READY'})
    result=ui._quality_gate_state()
    assert result['ready'] and result['status']=='READY'


def test_monitor_runs_only_in_real_worker_and_deduplicates_threads(tmp_path,monkeypatch):
    import local_runtime_health as health
    import threading
    monkeypatch.setenv('AI_ENGINE_WORKER','1')
    monkeypatch.setattr(sys,'argv',['streamlit'])
    assert health.start_worker_monitor() is False
    monkeypatch.setattr(sys,'argv',['worker_base.py'])
    monkeypatch.setitem(sys.modules,'runtime_supervisor',SimpleNamespace(CLAIM={'generation':'test','build':'build-test'}))
    db=tmp_path/'market.db'
    monkeypatch.setattr(health,'DB',str(db))
    monkeypatch.setattr(health,'source_fingerprint',lambda:'startup-fingerprint')
    monkeypatch.setattr(threading,'enumerate',lambda:[SimpleNamespace(name='asi-local-heartbeat'),SimpleNamespace(name='asi-runtime-watchdog')])
    monkeypatch.setattr(threading,'Thread',lambda **kwargs: pytest.fail('duplicate monitoring thread'))
    assert health.start_worker_monitor() is True
    con=sqlite3.connect(db);state=dict(con.execute('SELECT key,value FROM state'));con.close()
    assert state['local_worker_source_fingerprint']=='startup-fingerprint'
    assert state['local_worker_generation']=='test'
    assert state['local_worker_runtime_build']=='build-test'
    assert datetime.fromisoformat(state['local_worker_heartbeat_at']).tzinfo is not None


def test_old_worker_cannot_relabel_startup_fingerprint_as_new_source(tmp_path,monkeypatch):
    import local_runtime_health as health
    monkeypatch.setattr(health,'DB',str(tmp_path/'market.db'))
    monkeypatch.setattr(health,'source_fingerprint',lambda:'new-source')
    health.write_worker_heartbeat(pid=7,generation='old',build='old',fingerprint='old-source')
    assert health.health(now=datetime.now(timezone.utc))['status']=='LOCAL_BUILD_MISMATCH'


def test_monitor_starts_missing_services_and_watch_restarts_on_source_change(tmp_path,monkeypatch):
    import local_runtime_health as health
    import threading,time
    monkeypatch.setenv('AI_ENGINE_WORKER','1')
    monkeypatch.setattr(sys,'argv',['worker_base.py'])
    monkeypatch.setitem(sys.modules,'runtime_supervisor',SimpleNamespace(CLAIM={'generation':'g','build':'b','fingerprint':'boot'}))
    monkeypatch.setattr(health,'DB',str(tmp_path/'market.db'))
    monkeypatch.setattr(health,'source_fingerprint',lambda:'boot')
    monkeypatch.setattr(threading,'enumerate',lambda:[])
    targets={}
    class Thread:
        def __init__(self,target,name,daemon):
            assert daemon
            targets[name]=target
        def start(self):
            pass
    monkeypatch.setattr(threading,'Thread',Thread)
    assert health.start_worker_monitor()
    assert set(targets)=={'asi-local-heartbeat','asi-runtime-watchdog'}
    monkeypatch.setattr(time,'sleep',lambda seconds:None)
    monkeypatch.setattr(health,'source_fingerprint',lambda:'changed')
    restarted=[]
    class Restart(Exception):pass
    def restart(executable,args):
        restarted.append((executable,args))
        raise Restart
    monkeypatch.setattr(health.os,'execv',restart)
    with pytest.raises(Restart):targets['asi-runtime-watchdog']()
    assert restarted[0][1][1].endswith('worker_base.py')


def test_real_ui_blocks_action_cards_when_worker_missing(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    ui=quality_db(tmp_path,monkeypatch)
    import runtime_health
    monkeypatch.setattr(runtime_health,'remote_runtime_health',lambda:{'required':True,'ready':False,'status':'LOCAL_RUNTIME_MISSING','reason':'heartbeat missing'})
    app=AppTest.from_string('''
import pandas as pd
from dynamic_opportunity_ui import render_actionable_dynamic
render_actionable_dynamic(pd.DataFrame([{'ticker':'BBCA','state':'ENTRY READY','entry_status':'ACTIVE','candidate_mode':'STRICT'}]))
''').run(timeout=20)
    assert not app.exception
    assert any('Top-5 produksi ditahan' in w.value for w in app.warning)
    assert not any('?ticker=BBCA' in m.value for m in app.markdown)


def test_local_recovery_is_bounded_and_does_not_spawn_in_external_mode(monkeypatch):
    import local_runtime_health as health
    import runtime_health,subprocess
    monkeypatch.delenv('AI_ENGINE_WORKER',raising=False)
    monkeypatch.setattr(health,'_RECOVERY_LAST',None)
    monkeypatch.setattr(health,'health',lambda:{'status':'LOCAL_RUNTIME_MISSING'})
    monkeypatch.setattr(runtime_health,'external_runtime_expected',lambda:False)
    calls=[]
    monkeypatch.setattr(subprocess,'Popen',lambda *args,**kwargs:calls.append((args,kwargs)))
    assert health.request_worker_recovery() is True
    assert health.request_worker_recovery() is False
    assert len(calls)==1
    env=calls[0][1]['env']
    assert env['AI_ENGINE_WORKER']=='1' and env['PYTHONPATH'].split(health.os.pathsep)[0]==health.BASE
    monkeypatch.setattr(health,'_RECOVERY_LAST',None)
    monkeypatch.setattr(runtime_health,'external_runtime_expected',lambda:True)
    assert health.request_worker_recovery() is False and len(calls)==1


def test_healthy_or_engine_warming_worker_is_not_restarted(monkeypatch):
    import local_runtime_health as health
    import runtime_health,subprocess
    monkeypatch.delenv('AI_ENGINE_WORKER',raising=False)
    monkeypatch.setattr(health,'_RECOVERY_LAST',None)
    monkeypatch.setattr(runtime_health,'external_runtime_expected',lambda:False)
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('healthy/warming worker restarted'))
    for state in ['LOCAL_RUNTIME_READY','LOCAL_ENGINE_WARMING','LOCAL_ENGINE_STALLED']:
        monkeypatch.setattr(health,'health',lambda:{'status':state})
        assert health.request_worker_recovery() is False
