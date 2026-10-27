import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from validation.probability_calibration.experiment import choose,replay,HERE
from test_day_forecast import fixture


def test_selection_uses_validation_loss_and_prefers_cautious_weight_on_ties():
    rows=[{'day':'2026-10-07','actual':2,'p':[.1,.1,.8]}]
    weight,_=choose(rows,[0.,.5,1.])
    assert weight==1
    rows[0]['p']=[1/3]*3
    assert choose(rows,[0.,.5,1.])[0]==0


def test_future_target_changes_cannot_change_earlier_forecast():
    raw=fixture();modified=raw.copy()
    mask=pd.to_datetime(modified.ts,utc=True)>=pd.Timestamp('2026-10-27T09:30:00+07:00').tz_convert('UTC')
    modified.loc[mask,'close']*=1.2
    modified.loc[mask,'high']=np.maximum(modified.loc[mask,'high'],modified.loc[mask,'close']+1)
    a=replay(raw,'BBCA',['2026-10-27'],['09:30'])
    b=replay(modified,'BBCA',['2026-10-27'],['09:30'])
    assert a and np.allclose([r['p'] for r in a],[r['p'] for r in b])
    assert [r['actual'] for r in a]!=[r['actual'] for r in b]


def test_frozen_candidate_and_new_day_evidence_are_reproducible_and_not_promoted():
    manifest=json.loads((HERE/'frozen.json').read_text());report=json.loads((HERE/'results.json').read_text())
    acquisition=json.loads((HERE/'acquisition.json').read_text());rows=json.loads((HERE/'replay.json').read_text())
    assert pd.Timestamp(manifest['frozen_at'])<pd.Timestamp(acquisition['started_at'])
    for filename,key in [('protocol.json','protocol_sha256'),('experiment.py','engine_sha256')]:
        assert hashlib.sha256((HERE/filename).read_bytes()).hexdigest()==manifest[key]
    assert not manifest['production_enabled'] and not report['production_enabled']
    assert len(rows)==report['n']==128
    assert len({(r['ticker'],r['day'],r['slot']) for r in rows})==report['unique_outcomes']==58
    for r in rows:
        assert pd.Timestamp(r['training_end']).date()<pd.Timestamp(r['generated_at']).date()
        assert pd.Timestamp(r['feature_asof'])<=pd.Timestamp(r['generated_at'])
        assert pd.Timestamp(r['generated_at'])<=pd.Timestamp(r['day']+' '+r['slot'],tz='Asia/Jakarta')
        assert np.allclose(r['calibrated'],manifest['alpha']*np.asarray(r['p'])+(1-manifest['alpha'])/3)
    p=np.asarray([r['calibrated'] for r in rows]);y=np.asarray([r['actual'] for r in rows])
    assert report['candidate']['brier']==pytest.approx(((p-np.eye(3)[y])**2).sum(axis=1).mean())
    assert report['candidate']['brier']>report['r90']['brier']
    for c in report['coverage']:
        if c.get('input_sha256'):
            assert hashlib.sha256((HERE/'inputs'/(c['ticker']+'.csv')).read_bytes()).hexdigest()==c['input_sha256']
