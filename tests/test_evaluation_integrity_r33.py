import sqlite3
import pandas as pd


def _row(**extra):
    base={
        'ticker':'AAA','company':'AAA','rank':1,'decision':'BUY','candidate_mode':'STRICT',
        'state':'ENTRY READY','entry_status':'ACTIVE','opportunity_score':88.0,'ai_score':85.0,
        'p_up':0.72,'p_down':0.14,'confidence':0.8,'close':123.0,
        'entry_low':120.0,'entry_high':124.0,'stop_loss':114.0,'target_1':130.0,'target_2':138.0,
        'risk_reward':2.0,'risk':'LOW','market_regime':'BULLISH','reason':'unit',
        'intraday_fresh':True,'price_source':'LIVE_INTRADAY',
    }
    base.update(extra)
    return base


def test_actionability_live_requires_entry_ready_active_and_fresh():
    import recommendation_history as r
    live={'live':True,'status':'SESSION_1'}
    assert r._row_actionability(pd.Series(_row()),market=live) is True
    assert r._row_actionability(pd.Series(_row(intraday_fresh=False)),market=live) is False
    assert r._row_actionability(pd.Series(_row(state='MONITOR')),market=live) is False
    assert r._row_actionability(pd.Series(_row(entry_status='WAIT')),market=live) is False
    assert r._row_actionability(pd.Series(_row(candidate_mode='MONITOR_FALLBACK')),market=live) is False


def test_offmarket_buy_can_seed_next_session_shadow():
    import recommendation_history as r
    closed={'live':False,'status':'CLOSED'}
    assert r._row_actionability(pd.Series(_row(state='MONITOR',entry_status='WAIT',intraday_fresh=False,price_source='DAILY_CLOSE')),market=closed) is True


def test_live_stale_buy_is_recorded_but_never_seeded(tmp_path,monkeypatch):
    import recommendation_history as r
    db=tmp_path/'market.db'
    monkeypatch.setattr(r,'session_status',lambda cfg:{'live':True,'status':'SESSION_1'})
    df=pd.DataFrame([_row(state='MONITOR',entry_status='NEUTRAL',intraday_fresh=False,price_source='DAILY_CLOSE_STALE_INTRADAY')])
    result=r.record_snapshot(str(db),df,{},source_phase='full-universe')
    assert result['recommendations']==1
    assert result['shadow_seeded']==0
    con=sqlite3.connect(db)
    hist=con.execute('SELECT actionable_at_record,intraday_fresh,price_source,session_status FROM recommendation_history').fetchone()
    shadows=con.execute('SELECT COUNT(*) FROM shadow_positions').fetchone()[0]
    con.close()
    assert hist==(0,0,'DAILY_CLOSE_STALE_INTRADAY','SESSION_1')
    assert shadows==0


def test_live_fresh_entry_ready_seeds_shadow_and_records_provenance(tmp_path,monkeypatch):
    import recommendation_history as r
    db=tmp_path/'market.db'
    monkeypatch.setattr(r,'session_status',lambda cfg:{'live':True,'status':'SESSION_2'})
    result=r.record_snapshot(str(db),pd.DataFrame([_row()]),{},source_phase='full-universe')
    assert result['recommendations']==1
    assert result['shadow_seeded']==1
    con=sqlite3.connect(db)
    hist=con.execute('SELECT actionable_at_record,intraday_fresh,price_source,session_status FROM recommendation_history').fetchone()
    shadow=con.execute('SELECT ticker,status,entry_low,entry_high FROM shadow_positions').fetchone()
    con.close()
    assert hist==(1,1,'LIVE_INTRADAY','SESSION_2')
    assert shadow[0]=='AAA' and shadow[1]=='WAITING_ENTRY'
    assert shadow[2]==120.0 and shadow[3]==124.0


def test_non_live_buy_seeds_next_session_shadow_but_marks_session(tmp_path,monkeypatch):
    import recommendation_history as r
    db=tmp_path/'market.db'
    monkeypatch.setattr(r,'session_status',lambda cfg:{'live':False,'status':'CLOSED'})
    row=_row(state='MONITOR',entry_status='NEUTRAL',intraday_fresh=False,price_source='DAILY_CLOSE')
    result=r.record_snapshot(str(db),pd.DataFrame([row]),{},source_phase='full-universe')
    assert result['shadow_seeded']==1
    con=sqlite3.connect(db)
    hist=con.execute('SELECT actionable_at_record,session_status FROM recommendation_history').fetchone()
    con.close()
    assert hist==(1,'CLOSED')
