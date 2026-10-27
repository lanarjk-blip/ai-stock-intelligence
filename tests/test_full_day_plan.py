import copy
import json
import sqlite3
import pandas as pd
from test_day_forecast import fixture
from timemap_forecast import predict
from timemap_forecast_ui import (connect,record,saved,table,plan_coverage,prepare_watched,
                                 prospective_summary,promotion_gate)


def test_first_plan_and_later_revision_survive_reopen_without_overwriting(tmp_path):
    path=tmp_path/'market.db';d=fixture();morning=pd.Timestamp('2026-10-27T08:50:00+07:00')
    a=predict(d,'BBCA',morning);record(path,a)
    b=copy.deepcopy(a);b['input_hash']='new-input';b['generated_at']='2026-10-27T08:55:00+07:00'
    for r in b['rows']:
        if r['p'] is not None:r['p']=[.1,.2,.7]
    record(path,b)
    now=pd.Timestamp('2026-10-27T10:00:00+07:00')
    reopened=saved(path,'BBCA','2026-10-27')
    first=table(a,list(reversed(reopened)),now,revision='first')
    latest=table(a,reopened,now,revision='latest')
    assert first.iloc[0]['Dibuat WIB']=='27-10 08:50:00'
    assert latest.iloc[0]['Dibuat WIB']=='27-10 08:55:00'
    assert latest.iloc[0]['Prediksi UP % (uji)']=='70.00'
    assert first.iloc[0]['Prediksi UP % (uji)']!=latest.iloc[0]['Prediksi UP % (uji)']
    assert first.iloc[0]['Versi tercatat']==2
    assert plan_coverage(a,reopened,now)=={'expected':10,'recorded':10,'preopen':10,'missing':[]}
    assert not promotion_gate()['approved']


def test_no_old_day_or_other_ticker_or_model_is_displayed_as_today(tmp_path):
    d=fixture();now=pd.Timestamp('2026-10-27T08:50:00+07:00');a=predict(d,'BBCA',now)
    record(tmp_path/'market.db',a);records=saved(tmp_path/'market.db','BBCA','2026-10-27')
    for key,value in [('ticker','ANTM'),('trading_date','2026-10-26'),('model_version','OLD MODEL')]:
        corrupt=[dict(r,**{key:value}) for r in records]
        display=table(a,corrupt,now,revision='first')
        assert set(display['Prediksi UP % (uji)'])=={'—'}
        assert plan_coverage(a,corrupt,now)['recorded']==0


def test_opening_app_late_does_not_backfill_0900_forecast(tmp_path):
    now=pd.Timestamp('2026-10-27T10:05:00+07:00');bundle=predict(fixture(),'BBCA',now)
    path=tmp_path/'market.db';record(path,bundle);records=saved(path,'BBCA','2026-10-27')
    coverage=plan_coverage(bundle,records,now)
    assert coverage['preopen']==0 and '09:00' in coverage['missing']
    assert table(bundle,records,now,revision='first').iloc[0]['Dibuat WIB']=='—'
    assert all(pd.Timestamp(r['generated_at'])<=pd.Timestamp(r['slot']) for r in records)


def _history(path):
    con=connect(path);d=fixture();d['ticker']='ZZZZ';d['source']='TEST FIXTURE';d.ts=d.ts.astype(str)
    d.to_sql('intraday',con,index=False,if_exists='replace')
    return con


def test_more_than_twenty_watchlist_tickers_get_fair_preopen_preparation(tmp_path):
    path=tmp_path/'market.db';con=_history(path)
    for ticker in [f'A{i:03d}' for i in range(21)]+['ZZZZ']:
        con.execute('INSERT INTO timemap_forecast_watch VALUES(?,?)',(ticker,'2026-10-26T15:00:00+07:00'))
    con.commit();con.close();now=pd.Timestamp('2026-10-27T08:40:00+07:00')
    assert prepare_watched(path,now)=={'checked':20,'recorded':0}
    assert prepare_watched(path,now+pd.Timedelta(minutes=1))=={'checked':20,'recorded':10}
    con=sqlite3.connect(path)
    assert con.execute('SELECT COUNT(*) FROM timemap_forecast_prepare_state').fetchone()[0]==22
    con.close()
    assert len(saved(path,'ZZZZ','2026-10-27'))==10
    assert prospective_summary(saved(path,'ZZZZ','2026-10-27'))['n']==0


def test_completed_plan_skips_recomputation_and_next_day_prepares_again(tmp_path,monkeypatch):
    path=tmp_path/'market.db';con=_history(path)
    con.execute('INSERT INTO timemap_forecast_watch VALUES(?,?)',('ZZZZ','2026-10-26T15:00:00+07:00'));con.commit();con.close()
    now=pd.Timestamp('2026-10-27T08:40:00+07:00')
    assert prepare_watched(path,now)['recorded']==10
    import timemap_forecast_ui as ui
    original=ui.predict
    def no_retraining(*args,**kwargs):raise AssertionError('Complete preopen plan must be reused')
    monkeypatch.setattr(ui,'predict',no_retraining)
    assert prepare_watched(path,now+pd.Timedelta(minutes=1))=={'checked':0,'recorded':0}
    monkeypatch.setattr(ui,'predict',original)
    assert prepare_watched(path,now+pd.Timedelta(days=1))['recorded']==10
    assert {r['trading_date'] for r in saved(path,'ZZZZ','2026-10-28')}=={'2026-10-28'}
    assert {r['generated_at'] for r in saved(path,'ZZZZ','2026-10-27')}=={now.isoformat()}


def test_unknown_cached_history_cannot_make_a_full_day_plan(tmp_path):
    path=tmp_path/'market.db';con=_history(path)
    con.execute("UPDATE intraday SET source='LAST GOOD CACHE'")
    con.execute('INSERT INTO timemap_forecast_watch VALUES(?,?)',('ZZZZ','2026-10-26T15:00:00+07:00'));con.commit();con.close()
    assert prepare_watched(path,pd.Timestamp('2026-10-27T08:40:00+07:00'))=={'checked':1,'recorded':0}
    assert saved(path,'ZZZZ','2026-10-27')==[]


def test_revision_selector_displays_first_plan_and_can_show_updates(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import timemap_forecast_ui as ui
    path=tmp_path/'market.db';monkeypatch.setattr(ui,'DB_PATH',path)
    d=fixture();a=predict(d,'BBCA',pd.Timestamp('2026-10-27T08:40:00+07:00'));record(path,a)
    b=copy.deepcopy(a);b['generated_at']='2026-10-27T08:45:00+07:00';b['input_hash']='update'
    for r in b['rows']:
        if r['p'] is not None:r['p']=[.1,.2,.7]
    record(path,b)
    # Keep the stored test revisions stable during this rendering test.
    monkeypatch.setattr(ui,'predict',lambda *a,**k:dict(b,rows=[dict(r,p=None) for r in b['rows']]))
    app=AppTest.from_string('''
import pandas as pd
from test_day_forecast import fixture
from market_session import session_status
from timemap_forecast_ui import render_forecast
render_forecast(fixture(),'BBCA',session_status(now=pd.Timestamp('2026-10-27T08:50:00+07:00')))
''').run(timeout=20)
    assert not app.exception
    app.checkbox[0].check().run(timeout=20)
    assert app.dataframe[0].value.iloc[0]['Dibuat WIB']=='27-10 08:45:00'
    app.radio[0].set_value('Rencana pertama tercatat').run(timeout=20)
    assert app.dataframe[0].value.iloc[0]['Dibuat WIB']=='27-10 08:40:00'
    app.radio[0].set_value('Pembaruan terbaru').run(timeout=20)
    assert not app.exception
    assert app.dataframe[0].value.iloc[0]['Dibuat WIB']=='27-10 08:45:00'
    assert app.dataframe[0].value.iloc[0]['Prediksi UP % (uji)']=='70.00'
    assert len(saved(path,'BBCA','2026-10-27'))==20
