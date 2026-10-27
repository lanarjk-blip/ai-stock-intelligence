import sqlite3
import pandas as pd
import pytest
from live_evidence import read_intraday_context,valid_live_info,current_timestamp
from market_session import session_status


def db(tmp_path,stamp,**changes):
    path=tmp_path/'market.db'
    row={'ticker':'TEST','ts':stamp,'open':100.,'high':102.,'low':99.,'close':101.,'volume':1000.,'source':'TEST PROVIDER'}
    row.update(changes)
    con=sqlite3.connect(path);pd.DataFrame([row]).to_sql('intraday',con,index=False);con.close()
    return lambda:sqlite3.connect(path)


@pytest.mark.parametrize('now,prior',[
    ('2026-10-26T10:00:00+07:00','2026-10-23T15:00:00+07:00'),
    ('2026-10-27T10:00:00+07:00','2026-10-26T15:00:00+07:00'),
    ('2026-11-02T10:00:00+07:00','2026-10-30T15:00:00+07:00'),
    ('2027-01-04T10:00:00+07:00','2026-12-30T15:00:00+07:00'),
    ('2026-10-27T13:31:00+07:00','2026-10-27T11:59:00+07:00'),
])
def test_prior_day_month_year_holiday_and_session_tape_is_not_live(tmp_path,now,prior):
    result=read_intraday_context(db(tmp_path,prior),'TEST',now=pd.Timestamp(now))
    assert not result['fresh'] and result['price'] is None
    assert not current_timestamp(prior,session_status(now=pd.Timestamp(now)))


@pytest.mark.parametrize('changes',[
    {'ts':'2026-10-27T10:01:00+07:00'}, {'ts':'2026-10-27T09:50:00+07:00'},
    {'source':'LAST GOOD CACHE'}, {'source':None}, {'volume':0}, {'volume':None},
    {'close':-1}, {'close':float('inf')}, {'low':105}, {'ticker':'OTHER'},
])
def test_future_stale_cached_untraded_invalid_or_other_ticker_cannot_be_fresh(tmp_path,changes):
    result=read_intraday_context(db(tmp_path,'2026-10-27T09:58:00+07:00',**changes),'TEST',now=pd.Timestamp('2026-10-27T10:00:00+07:00'))
    assert not result['fresh'] and result['price'] is None


@pytest.mark.parametrize('module,name',[('dynamic_engine','_latest_intraday_live_price'),('hold_engine','_live_intraday_context')])
def test_dynamic_and_hold_share_actual_timestamp_guards(tmp_path,monkeypatch,module,name):
    import importlib
    m=importlib.import_module(module)
    monkeypatch.setattr(m,'dbcon',db(tmp_path,'2026-10-27T09:58:00+07:00'))
    now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    result=getattr(m,name)('TEST',now=now)
    assert result['fresh'] and result['price']==101
    assert valid_live_info('TEST',result,session_status(now=now))
    assert not valid_live_info('OTHER',result,session_status(now=now))
    # Reopening the next day does not renew the saved candle.
    result=getattr(m,name)('TEST',now=now+pd.Timedelta(days=1))
    assert not result['fresh'] and result['price'] is None


def test_ai_ask_does_not_combine_yesterdays_entry_with_todays_price():
    import app
    now=pd.Timestamp('2026-10-27T10:00:00+07:00');session=session_status(now=now)
    daily={'ticker':'TEST','close':100.,'risk':'LOW','decision':'BUY'}
    old={'ticker':'TEST','generated_at':'2026-10-26T10:00:00+07:00','close':101.,'state':'ENTRY READY','entry_status':'ACTIVE','candidate_mode':'STRICT'}
    info={'ticker':'TEST','fresh':True,'source':'TEST PROVIDER','price':101.,'ts':'2026-10-27T09:58:00+07:00'}
    snap=app.build_ai_ask_snapshot(daily,old,'TEST',session=session,live_info=info)
    assert snap['close']==101 and snap['state']=='WAIT'
    from ai_stock_ask import answer_stock
    assert answer_stock(snap,'TODAY',now=now)['action']=='WAIT'
    old['generated_at']='2026-10-27T09:59:00+07:00';old['close']=102
    snap=app.build_ai_ask_snapshot(daily,old,'TEST',session=session,live_info=info)
    assert snap['state']=='WAIT' # price moved since Dynamic snapshot
    old['ticker']='OTHER';old['close']=101
    assert app.build_ai_ask_snapshot(daily,old,'TEST',session=session,live_info=info)['state']=='WAIT'


def test_refresh_flag_cannot_make_old_price_or_old_answer_new():
    import app
    from ai_stock_ask import answer_stock
    now=pd.Timestamp('2026-10-27T10:00:00+07:00');session=session_status(now=now)
    info={'ticker':'TEST','fresh':True,'source':'TEST PROVIDER','price':101.,'ts':'2026-10-26T09:58:00+07:00'}
    shown=app.resolve_stock_display_price('TEST',100.,session=session,live_info=info)
    assert not shown['fresh'] and shown['source']=='DAILY_CLOSE'
    snap={'ticker':'TEST','decision':'BUY','ai_score':90.,'p_up':.8,'risk':'LOW','live_required':True,'intraday_fresh':True,
          'state':'ENTRY READY','entry_status':'ACTIVE','generated_at':'2026-10-26T10:00:00+07:00','live_info':info,'checked_at':now.isoformat()}
    assert answer_stock(snap,'TODAY',now=now)['action']=='WAIT'


def test_opportunity_screen_downgrades_yesterdays_buy_card(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import dynamic_opportunity_ui as ui
    import market_session
    now=pd.Timestamp('2026-10-27T10:00:00+07:00');original=market_session.session_status
    monkeypatch.setattr(market_session,'session_status',lambda cfg=None,now=None:original(cfg,now=now if now is not None else pd.Timestamp('2026-10-27T10:00:00+07:00')))
    monkeypatch.setattr(ui,'_quality_gate_state',lambda:{'known':True,'ready':True,'status':'READY','ready_ratio':1})
    app=AppTest.from_string('''
import pandas as pd
from dynamic_opportunity_ui import render_actionable_dynamic
render_actionable_dynamic(pd.DataFrame([dict(ticker='TEST',state='ENTRY READY',entry_status='ACTIVE',candidate_mode='STRICT',generated_at='2026-10-26T10:00:00+07:00',intraday_at='2026-10-26T09:58:00+07:00',intraday_fresh=True,price_source='LIVE_INTRADAY')]))
''').run(timeout=20)
    assert not app.exception
    text='\n'.join(x.value for x in app.markdown)
    assert 'asi-live-card ready' not in text
    assert 'MONITOR ONLY' in text
