import hashlib,json,shutil
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from validation.day_candidates import experiment as exp
from validation.day_candidates.frozen import infer
from test_day_forecast import fixture


def test_future_target_changes_do_not_change_issue_features():
    raw=fixture();now=pd.Timestamp('2026-10-27T23:00:00+07:00')
    a=exp.features(raw,'BBCA',now)
    changed=raw.copy();mask=pd.to_datetime(changed.ts,utc=True)>=pd.Timestamp('2026-10-27T09:30:00+07:00').tz_convert('UTC')
    changed.loc[mask,'close']*=1.2;changed.loc[mask,'high']*=1.3
    b=exp.features(changed,'BBCA',now)
    a=a[(a.day=='2026-10-27')&(a.issue=='09:30')].reset_index(drop=True)
    b=b[(b.day=='2026-10-27')&(b.issue=='09:30')].reset_index(drop=True)
    pd.testing.assert_frame_equal(a[exp.FEATURES],b[exp.FEATURES])
    assert len(a)>0 and not a.label.equals(b.label)


def test_validation_selection_does_not_take_final_accuracy_as_input():
    assert exp.choose({'a':{'brier':.65,'directional_accuracy':.2},'b':{'brier':.67,'directional_accuracy':.9}})=='a'


def test_frozen_candidate_replays_all_final_instances_without_training():
    root=Path(exp.__file__).parent
    replay=json.loads((root/'replay.json').read_text())
    frame=pd.DataFrame([r['features'] for r in replay],columns=exp.FEATURES)
    expected=np.asarray([r['p'] for r in replay])
    actual=infer(frame)
    assert len(replay)==1008 and np.allclose(actual,expected,atol=1e-7)
    manifest=json.loads((root/'frozen.json').read_text())
    assert hashlib.sha256((root/'experiment.py').read_bytes()).hexdigest()==manifest['feature_engine_sha256']
    assert not manifest['production_enabled'] and not manifest['independent_prospective_evidence']
    assert hashlib.sha256((root/'protocol.json').read_bytes()).hexdigest()==manifest['protocol_sha256']
    for r in replay:
        issue=pd.Timestamp(r['generated_at'])
        assert pd.Timestamp(r['feature_asof'])<=issue
        assert pd.Timestamp(r['training_end']).date()<issue.date()
        assert issue<=pd.Timestamp(r['day']+' '+r['slot'],tz='Asia/Jakarta')


def test_component_tampering_is_rejected(tmp_path):
    root=Path(exp.__file__).parent
    manifest=json.loads((root/'frozen.json').read_text())
    for name in ['frozen.json','protocol.json',*manifest['components']]:shutil.copy(root/name,tmp_path/name)
    component=tmp_path/next(iter(manifest['components']))
    component.write_text(component.read_text()+' ')
    with pytest.raises(ValueError,match='checksum'):
        infer(pd.DataFrame([[0]*len(exp.FEATURES)],columns=exp.FEATURES),tmp_path)


def test_failed_candidate_cannot_replace_production_projection():
    from timemap_forecast_ui import promotion_gate
    root=Path(exp.__file__).parent
    report=json.loads((root/'results.json').read_text())
    selected=report['selected_on_validation']
    assert report['final_metrics'][selected]['directional_accuracy']<report['final_metrics']['R90']['directional_accuracy']
    assert report['promotion'].startswith('BLOCKED')
    assert not promotion_gate()['approved']
