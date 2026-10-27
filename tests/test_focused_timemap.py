import pandas as pd
import pytest
from test_day_forecast import fixture
from timemap_forecast import predict
from timemap_forecast_ui import record,saved
from focused_timemap import compact_plan


def plan(tmp_path):
    now=pd.Timestamp('2026-10-27T08:50:00+07:00')
    bundle=predict(fixture(),'BBCA',now)
    path=tmp_path/'market.db';record(path,bundle)
    return now,bundle,saved(path,'BBCA','2026-10-27')


def test_compact_table_has_numeric_plan_without_duplicate_history(tmp_path):
    now,bundle,records=plan(tmp_path)
    short,detail,coverage=compact_plan(bundle,records,now)
    assert list(short.columns)==['Jam WIB','UP % (uji)','DATAR % (uji)','DOWN % (uji)','Realisasi','Status']
    assert coverage['recorded']==10
    numeric=short[short['UP % (uji)']!='—']
    assert len(numeric)==10
    for _,r in numeric.iterrows():
        assert sum(float(r[c]) for c in ['UP % (uji)','DATAR % (uji)','DOWN % (uji)'])==pytest.approx(100,abs=.02)
    assert set(numeric.Status)=={'Proyeksi uji • belum terpercaya'}


def test_unavailable_input_hides_unusable_numbers_but_preserves_original_record(tmp_path):
    now,bundle,records=plan(tmp_path)
    bundle['reason']='DATA STALE'
    short,detail,_=compact_plan(bundle,records,now)
    assert set(short['UP % (uji)'])=={'—'}
    assert 'Arsip proyeksi • WAIT' in set(short.Status)
    assert any(detail['Prediksi UP % (uji)']!='—')
    assert {r['generated_at'] for r in records}=={now.isoformat()}


@pytest.mark.parametrize('field,value',[('ticker','MPXL'),('trading_date','2026-10-26')])
def test_other_ticker_or_yesterday_never_becomes_today(tmp_path,field,value):
    now,bundle,records=plan(tmp_path)
    records=[dict(r,**{field:value}) for r in records]
    short,_,coverage=compact_plan(bundle,records,now)
    assert set(short['UP % (uji)'])=={'—'} and coverage['recorded']==0


def test_focused_screen_search_switch_and_original_date(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import focused_timemap as focused
    import timemap_forecast_ui as ui
    import local_runtime_health
    from market_session import session_status
    monkeypatch.setattr(local_runtime_health,'request_worker_recovery',lambda:False)
    monkeypatch.setattr(ui,'DB_PATH',tmp_path/'market.db')
    now=pd.Timestamp('2026-10-27T08:50:00+07:00')
    monkeypatch.setattr(focused,'session_status',lambda:session_status(now=now))
    def tape(ticker):
        d=fixture(ticker)
        return d[pd.to_datetime(d.ts,utc=True)<now.tz_convert('UTC')].copy()
    monkeypatch.setattr(focused,'load',tape)
    app=AppTest.from_string('''
import pandas as pd
from focused_timemap import render_focused_app
u=pd.DataFrame([dict(ticker='BBCA',company='Bank Central Asia'),dict(ticker='ANTM',company='Aneka Tambang')])
render_focused_app(lambda:(u,'fixture'),lambda u,q,n:u[u.ticker.str.contains(q.upper())])
''').run(timeout=30)
    assert not app.exception and len(app.dataframe)==0
    app.text_input[0].set_value('BBCA').run(timeout=30)
    assert not app.exception and len(app.dataframe)==1
    assert any('BELUM ADA PREDIKSI TERPERCAYA' in x.value for x in app.warning)
    assert any('26-10-2026' in x.value for x in app.caption)
    assert any('27 Oktober 2026' in x.value for x in app.caption)
    assert any('Data historis' in x.value for x in app.info)
    assert len(app.tabs)==0 and len(app.checkbox)==0
    app.text_input[0].set_value('ANTM').run(timeout=30)
    assert not app.exception and len(app.dataframe)==1
    assert any('ANTM · Aneka Tambang' in x.value for x in app.markdown)
    assert not any('BBCA ·' in x.value for x in app.markdown)
    app.text_input[0].set_value('INVALID').run(timeout=30)
    assert not app.exception and len(app.dataframe)==0
