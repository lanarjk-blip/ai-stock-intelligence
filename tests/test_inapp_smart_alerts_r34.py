import json
import sqlite3

import pandas as pd


def _dyn(**extra):
    base={
        'ticker':'AAA','company':'AAA','rank':3,'decision':'BUY','candidate_mode':'STRICT',
        'state':'ENTRY READY','entry_status':'ACTIVE','intraday_fresh':True,
        'price_source':'LIVE_INTRADAY','risk':'LOW','ai_score':82.0,'opportunity_score':80.0,
    }
    base.update(extra)
    return base


def test_entry_active_alert_requires_live_actionability():
    import smart_alerts as s
    old=_dyn(entry_status='WAIT',state='DEVELOPING',intraday_fresh=False,live_required=True)
    cur=_dyn(entry_status='ACTIVE',state='MONITOR',intraday_fresh=False,live_required=True)
    alerts=s.evaluate_transition(old,cur)
    assert 'ENTRY_WINDOW' not in [a['alert_type'] for a in alerts]

    cur2=_dyn(entry_status='ACTIVE',state='ENTRY READY',intraday_fresh=True,live_required=True)
    alerts2=s.evaluate_transition(old,cur2)
    assert 'ENTRY_WINDOW' in [a['alert_type'] for a in alerts2]


def test_monitor_fallback_never_announces_new_active_entry():
    import smart_alerts as s
    old=_dyn(entry_status='WAIT',state='MONITOR',live_required=True)
    cur=_dyn(entry_status='ACTIVE',state='ENTRY READY',candidate_mode='MONITOR_FALLBACK',live_required=True)
    types=[a['alert_type'] for a in s.evaluate_transition(old,cur)]
    assert 'ENTRY_WINDOW' not in types


def test_first_snapshot_seeds_state_without_alert(tmp_path):
    import smart_alerts as s
    db=tmp_path/'market.db'
    d=pd.DataFrame([_dyn()])
    result=s.sync_inapp_alerts(str(db),d,config={},phase='full-universe',live_required=True)
    assert result['status']=='SEEDED'
    assert result['alerts']==0
    con=sqlite3.connect(db)
    assert con.execute('SELECT COUNT(*) FROM smart_alert_state').fetchone()[0]==1
    assert con.execute('SELECT COUNT(*) FROM alert_history').fetchone()[0]==0
    con.close()


def test_material_second_snapshot_persists_inapp_alerts(tmp_path):
    import smart_alerts as s
    db=tmp_path/'market.db'
    first=pd.DataFrame([_dyn(rank=8,decision='WATCH',state='DEVELOPING',entry_status='WAIT',intraday_fresh=False,ai_score=65,opportunity_score=64)])
    second=pd.DataFrame([_dyn(rank=3,decision='BUY',state='ENTRY READY',entry_status='ACTIVE',intraday_fresh=True,ai_score=82,opportunity_score=81)])
    s.sync_inapp_alerts(str(db),first,config={},phase='full-universe',live_required=True)
    result=s.sync_inapp_alerts(str(db),second,config={},phase='full-universe',live_required=True)
    assert result['status']=='UPDATED'
    assert result['alerts']>=2
    con=sqlite3.connect(db)
    types={r[0] for r in con.execute('SELECT alert_type FROM alert_history').fetchall()}
    con.close()
    assert 'TOP_5_ENTRY' in types
    assert 'ENTRY_WINDOW' in types


def test_live_stale_snapshot_can_record_info_but_not_entry_alert(tmp_path):
    import smart_alerts as s
    db=tmp_path/'market.db'
    first=pd.DataFrame([_dyn(decision='WATCH',state='DEVELOPING',entry_status='WAIT',intraday_fresh=False,ai_score=70)])
    stale=pd.DataFrame([_dyn(decision='BUY',state='MONITOR',entry_status='ACTIVE',intraday_fresh=False,ai_score=82)])
    s.sync_inapp_alerts(str(db),first,config={},live_required=True)
    s.sync_inapp_alerts(str(db),stale,config={},live_required=True)
    con=sqlite3.connect(db)
    rows=con.execute('SELECT alert_type,severity FROM alert_history').fetchall()
    con.close()
    types=[r[0] for r in rows]
    assert 'ENTRY_WINDOW' not in types
    assert 'DECISION' in types
    assert dict(rows).get('DECISION')=='MEDIUM'


def test_disappearance_marks_absent_and_reentry_is_detected(tmp_path):
    import smart_alerts as s
    db=tmp_path/'market.db'
    current=pd.DataFrame([_dyn(rank=3)])
    other=pd.DataFrame([_dyn(ticker='BBB',company='BBB',rank=1)])
    s.sync_inapp_alerts(str(db),current,config={},live_required=True)
    s.sync_inapp_alerts(str(db),other,config={},live_required=True)
    result=s.sync_inapp_alerts(str(db),current,config={},live_required=True)
    assert result['alerts']>=1
    con=sqlite3.connect(db)
    types={r[0] for r in con.execute("SELECT alert_type FROM alert_history WHERE ticker='AAA'").fetchall()}
    con.close()
    assert 'TOP_5_ENTRY' in types


def test_hold_context_is_merged_into_state(tmp_path):
    import smart_alerts as s
    db=tmp_path/'market.db'
    d=pd.DataFrame([_dyn()])
    h=pd.DataFrame([{'ticker':'AAA','state':'HOLD','overnight_state':'VALID','next_session_bias':'POSITIVE'}])
    s.sync_inapp_alerts(str(db),d,h,config={},live_required=False)
    con=sqlite3.connect(db)
    payload=json.loads(con.execute("SELECT snapshot_json FROM smart_alert_state WHERE ticker='AAA'").fetchone()[0])
    con.close()
    assert payload['hold_state']=='HOLD'
    assert payload['overnight_state']=='VALID'
    assert payload['next_session_bias']=='POSITIVE'
