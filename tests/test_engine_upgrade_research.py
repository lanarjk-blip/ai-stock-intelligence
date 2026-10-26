import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from validation.engine_upgrade import train
from validation.engine_upgrade.frozen import infer


def data(day):
    frame=pd.DataFrame([{**{c:0. for c in train.FEATURES},'label':label,'day':pd.Timestamp(day).date(),
                         'ts':pd.Timestamp(day+'T03:00Z'),'feature_asof':pd.Timestamp(day+'T02:30Z'),
                         'ticker':'BBCA','slot':'10:00'} for label in [0,1,2]])
    return frame


def test_training_boundary_and_feature_availability_are_enforced():
    earlier,later=data('2026-10-13'),data('2026-10-14')
    train.validate_split(earlier,later)
    with pytest.raises(ValueError):train.validate_split(later,earlier)
    with pytest.raises(ValueError):train.validate_split(earlier,earlier)
    later.loc[0,'feature_asof']=pd.Timestamp('2026-10-15T00:00Z')
    with pytest.raises(ValueError):train.validate_split(earlier,later)


def test_baseline_does_not_use_test_labels_or_another_ticker():
    training=data('2026-10-13');test=data('2026-10-14')
    expected=train.baselines(training,test)
    test['label']=2
    unrelated=data('2026-10-13');unrelated['ticker']='MPXL';unrelated['label']=2
    actual=train.baselines(pd.concat([training,unrelated]),test)
    for name in actual:np.testing.assert_allclose(actual[name],expected[name])


def test_validation_selects_and_archive_never_promotes_even_perfect_scores():
    validation={name:{'brier':.5+i*.01} for i,name in enumerate(train.NAMES)}
    assert train.choose(validation)=='balanced_logistic'
    assert train.promotion_decision(False,{'slot':[.1,.2]},True).startswith('NO PROMOTION')
    assert train.promotion_decision(True,{'slot':[-.1,.2]},True).startswith('NO PROMOTION')
    assert train.promotion_decision(True,{'slot':[.1,.2]},False).startswith('NO PROMOTION')
    assert train.promotion_decision(True,{'slot':[.1,.2]},True).startswith('REVIEW ONLY')


def test_invalid_model_probability_cannot_be_scored_as_forecast():
    class Invalid:
        classes_=np.array([0,1,2])
        def predict_proba(self,x):return np.full((len(x),3),np.nan)
    with pytest.raises(ValueError):train.probabilities(Invalid(),data('2026-10-14')[train.FEATURES])


def test_portable_weights_verify_identity_and_do_not_require_training(tmp_path):
    protocol=tmp_path/'protocol.json';protocol.write_text('{}')
    state=dict(type='balanced_logistic',features=['x'],classes=[0,1,2],scaler_mean=[0],scaler_scale=[1],
               coef=[[0],[0],[0]],intercept=[0,0,0])
    model=tmp_path/'frozen_balanced_logistic.json';model.write_text(json.dumps(state))
    manifest=dict(feature_names=['x'],protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
                  components={model.name:hashlib.sha256(model.read_bytes()).hexdigest()})
    (tmp_path/'frozen_candidate.json').write_text(json.dumps(manifest))
    np.testing.assert_allclose(infer(pd.DataFrame({'x':[1.,2.]}),tmp_path),np.full((2,3),1/3))
    model.write_text('{}')
    with pytest.raises(ValueError,match='checksum'):infer(pd.DataFrame({'x':[1.]}),tmp_path)


def test_saved_experiment_remains_offline_with_normalized_prior_only_replays():
    directory=Path('validation/engine_upgrade')
    report=json.loads((directory/'results.json').read_text())
    rows=json.loads((directory/'replay.json').read_text())
    assert not report['production_enabled'] and report['decision'].startswith('NO PROMOTION')
    assert report['selected_on_validation']==train.choose(report['validation'])
    assert len(rows)==report['test'][report['selected_on_validation']]['n']
    for row in rows:
        assert pd.Timestamp(row['feature_asof'])<=pd.Timestamp(row['candle_start'])
        assert row['historical_replay'] and not row['live_forecast']
        for p in row['probabilities'].values():
            assert np.isfinite(p).all() and min(p)>=0 and sum(p)==pytest.approx(1)
