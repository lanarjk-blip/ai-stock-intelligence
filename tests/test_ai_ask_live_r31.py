import os
import pandas as pd

os.environ.setdefault('AI_ENGINE_WORKER','1')


def _strong(**extra):
    base={
        'ticker':'AAA','decision':'BUY','ai_score':85.0,'jst_p_up':0.72,
        'risk':'LOW','market_regime':'BULLISH','close':100.0,
        'entry_low':98.0,'entry_high':102.0,'stop_loss':94.0,
        'target_1':108.0,'target_2':115.0,
    }
    base.update(extra)
    return base


def test_today_daily_buy_without_live_dynamic_confirmation_is_wait():
    import ai_stock_ask
    answer=ai_stock_ask.answer_stock(_strong(), 'TODAY')
    assert answer['action']=='WAIT'
    assert 'Market reguler tidak sedang live' in answer['guardrail']


def test_today_live_stale_intraday_is_wait_even_if_entry_ready():
    import ai_stock_ask
    snap=_strong(state='ENTRY READY',entry_status='ACTIVE',live_required=True,intraday_fresh=False,market_status='SESSION_1')
    answer=ai_stock_ask.answer_stock(snap,'TODAY',now=pd.Timestamp('2026-10-15T09:32:00+07:00'))
    assert answer['action']=='WAIT'
    assert 'belum segar' in answer['guardrail']


def test_today_live_fresh_entry_ready_can_buy():
    import ai_stock_ask
    snap=_strong(state='ENTRY READY',entry_status='ACTIVE',live_required=True,intraday_fresh=True,market_status='SESSION_1',price_source='LIVE_INTRADAY')
    snap.update(generated_at='2026-10-15T09:31:00+07:00',live_info={'ticker':'AAA','source':'TEST PROVIDER','fresh':True,'price':100.,'ts':'2026-10-15T09:30:00+07:00'})
    answer=ai_stock_ask.answer_stock(snap,'TODAY',now=pd.Timestamp('2026-10-15T09:32:00+07:00'))
    assert answer['action']=='BUY'
    assert answer['intraday_fresh'] is True
    assert answer['price_source']=='LIVE_INTRADAY'


def test_today_monitor_fallback_never_upgrades_to_buy():
    import ai_stock_ask
    snap=_strong(state='ENTRY READY',entry_status='ACTIVE',live_required=True,intraday_fresh=True,candidate_mode='MONITOR_FALLBACK')
    answer=ai_stock_ask.answer_stock(snap,'TODAY')
    assert answer['action']=='WAIT'
    assert 'MONITOR' in answer['guardrail']


def test_tomorrow_strong_outlook_does_not_require_current_live_entry():
    import ai_stock_ask
    answer=ai_stock_ask.answer_stock(_strong(market_regime='BULLISH'),'TOMORROW')
    assert answer['action']=='BUY'


def test_ai_ask_snapshot_dynamic_and_live_price_override_daily():
    import app
    universal=pd.Series(_strong(close=100.0,entry_low=97.0,entry_high=101.0))
    dynamic=pd.Series({
        'ticker':'AAA','close':123.0,'generated_at':'2026-10-15T09:31:00+07:00','entry_low':118.0,'entry_high':124.0,
        'stop_loss':112.0,'target_1':130.0,'target_2':138.0,
        'entry_status':'ACTIVE','state':'ENTRY READY','candidate_mode':'STRICT',
        'decision':'BUY','ai_score':88.0,'risk':'LOW','market_regime':'BULLISH',
    })
    session={'status':'SESSION_1','live':True,'calendar_verified':True,'market_date':'2026-10-15','wib':'2026-10-15T09:32:00+07:00'}
    live_info={'ticker':'AAA','source':'TEST PROVIDER','fresh':True,'price':123.0,'ts':'2026-10-15T02:30:00+00:00'}
    snapshot=app.build_ai_ask_snapshot(universal,dynamic,'AAA',session=session,live_info=live_info)
    assert snapshot['close']==123.0
    assert snapshot['entry_low']==118.0
    assert snapshot['state']=='ENTRY READY'
    assert snapshot['entry_status']=='ACTIVE'
    assert snapshot['live_required'] is True
    assert snapshot['intraday_fresh'] is True
    assert snapshot['price_source']=='LIVE_INTRADAY'


def test_ai_ask_snapshot_marks_live_missing_bar_not_actionable():
    import app
    snapshot=app.build_ai_ask_snapshot(
        pd.Series(_strong()), None, 'AAA',
        session={'status':'SESSION_1','live':True},
        live_info={'fresh':False,'price':None,'ts':''},
    )
    assert snapshot['live_required'] is True
    assert snapshot['intraday_fresh'] is False
    import ai_stock_ask
    assert ai_stock_ask.answer_stock(snapshot,'TODAY')['action']=='WAIT'
