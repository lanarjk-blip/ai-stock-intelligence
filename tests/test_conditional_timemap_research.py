import importlib.util
from pathlib import Path
import pandas as pd
import pytest

spec=importlib.util.spec_from_file_location('conditional',Path('validation/quality/conditional_models.py'))
research=importlib.util.module_from_spec(spec);spec.loader.exec_module(research)


def frame():
    return pd.read_csv('validation/acceptance/inputs/BBCA.csv')


def test_target_and_future_candle_fields_cannot_enter_features():
    raw=frame();cutoff=pd.Timestamp('2026-10-24T03:00Z')
    before=research.features(raw,'BBCA',cutoff)
    target=before.iloc[len(before)//2].ts
    changed=raw.copy();mask=pd.to_datetime(changed.ts,utc=True)>=target
    changed.loc[mask,['open','high','low','close']]*=2
    changed.loc[mask,'volume']*=3
    after=research.features(changed,'BBCA',cutoff)
    cols=['ts','feature_asof']+research.FEATURES
    pd.testing.assert_frame_equal(before.loc[before.ts<=target,cols],after.loc[after.ts<=target,cols])
    assert (before.feature_asof<=before.ts).all()


def test_ticker_features_do_not_depend_on_another_ticker_prices():
    a=research.features(frame(),'BBCA',pd.Timestamp('2026-10-24T03:00Z'))
    assert (a.ticker=='BBCA').all() and (a.ticker_bbca==1).all()
    assert not any(c in research.FEATURES for c in ['open','high','low','close','volume','label'])


def test_selection_uses_validation_only_and_split_has_disjoint_days():
    data=research.features(frame(),'BBCA',pd.Timestamp('2026-10-24T03:00Z'))
    train,val,test=research.split_days(data)
    assert max(train)<min(val) and max(val)<min(test) and len(test)==8
    scores={name:{'brier':i} for i,name in enumerate(research.NAMES)}
    assert research.select(scores)=='logistic'
    with pytest.raises(ValueError):research.split_days(data[data.day.isin(train[:5])])
