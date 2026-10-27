import copy
import sqlite3
import pandas as pd
import pytest
from test_day_forecast import fixture
from timemap_forecast import predict
from timemap_forecast_ui import record,saved,table,evaluate


def issued(tmp_path):
    now=pd.Timestamp('2026-10-27T08:50:00+07:00')
    bundle=predict(fixture(),'BBCA',now)
    path=tmp_path/'ledger.db';record(path,bundle)
    return path,bundle,now


def test_unchanged_refresh_does_not_duplicate_or_redate_predictions(tmp_path):
    path,bundle,now=issued(tmp_path)
    refreshed=copy.deepcopy(bundle);refreshed['generated_at']=(now+pd.Timedelta(minutes=1)).isoformat()
    record(path,refreshed)
    rows=saved(path,'BBCA','2026-10-27')
    assert len(rows)==10
    assert {r['generated_at'] for r in rows}=={now.isoformat()}


@pytest.mark.parametrize('later',['2026-10-28T10:00:00+07:00','2026-11-02T10:00:00+07:00','2027-01-04T10:00:00+07:00'])
def test_even_old_bundle_and_old_records_cannot_render_as_current_day(tmp_path,later):
    path,bundle,now=issued(tmp_path)
    rows=saved(path,'BBCA','2026-10-27')
    shown=table(bundle,rows,pd.Timestamp(later))
    assert set(shown['Prediksi UP % (uji)'])=={'—'}
    assert len(saved(path,'BBCA','2026-10-27'))==10


@pytest.mark.parametrize('field,value',[
    ('generated_at','2026-10-26T08:50:00+07:00'),
    ('generated_at','2026-10-27T08:50:00'),
    ('feature_asof','2026-10-27T09:30:00+07:00'),
    ('latest_candle','2026-10-27T09:30:00+07:00'),
    ('source','LAST GOOD CACHE'),
])
def test_write_rejects_relabelled_or_unverifiable_evidence(tmp_path,field,value):
    path,bundle,now=issued(tmp_path)
    changed=dict(bundle,**{field:value})
    with pytest.raises(ValueError):record(path,changed)
    assert len(saved(path,'BBCA','2026-10-27'))==10


@pytest.mark.parametrize('field,value',[
    ('generated_at','2026-10-26T08:50:00+07:00'),
    ('feature_asof','2026-10-27T09:30:00+07:00'),
    ('latest_candle','2026-10-27T09:30:00+07:00'),
    ('training_end','2026-10-27'),
])
def test_read_rejects_damaged_legacy_rows(tmp_path,field,value):
    path,bundle,now=issued(tmp_path)
    rows=[dict(r,**{field:value}) for r in saved(path,'BBCA','2026-10-27')]
    assert set(table(bundle,rows,now)['Prediksi UP % (uji)'])=={'—'}


def test_settled_intervals_are_not_recomputed_on_refresh(tmp_path,monkeypatch):
    import timemap_forecast_ui as ui
    path,bundle,now=issued(tmp_path)
    end=pd.Timestamp('2026-10-27T16:00:00+07:00')
    tape=fixture()
    # Last 15:00 observation only certifies earlier completed intervals.
    evaluate(path,'BBCA',tape,end)
    before=saved(path,'BBCA','2026-10-27')
    assert sum(r['actual_label'] is not None for r in before)==9
    calls=[]
    original=ui.full_regular_bar
    monkeypatch.setattr(ui,'full_regular_bar',lambda *a,**kw:(calls.append(a),original(*a,**kw))[1])
    evaluate(path,'BBCA',tape,end+pd.Timedelta(minutes=1))
    assert calls==[]
    assert saved(path,'BBCA','2026-10-27')==before


@pytest.mark.parametrize('before,after',[
    ('2026-10-23T08:50:00+07:00','2026-10-26T10:00:00+07:00'),
    ('2026-10-26T08:50:00+07:00','2026-10-27T10:00:00+07:00'),
])
def test_friday_monday_and_monday_tuesday_keep_history_separate(tmp_path,before,after):
    now=pd.Timestamp(before);bundle=predict(fixture(),'BBCA',now)
    path=tmp_path/'rollover.db';record(path,bundle)
    rows=saved(path,'BBCA',now.date().isoformat())
    assert rows
    assert set(table(bundle,rows,pd.Timestamp(after))['Prediksi UP % (uji)'])=={'—'}
    assert saved(path,'BBCA',pd.Timestamp(after).date().isoformat())==[]
    assert saved(path,'BBCA',now.date().isoformat())==rows
