"""Read-only JSON candidate replay. No training, networking or production use."""
import hashlib
import json
from pathlib import Path

import numpy as np


def _softmax(values):
    exp = np.exp(values-values.max(axis=1,keepdims=True))
    return exp/exp.sum(axis=1,keepdims=True)


def infer(frame, directory=None):
    directory = Path(directory) if directory else Path(__file__).parent
    manifest = json.loads((directory/'frozen_candidate.json').read_text())
    if hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest() != manifest['protocol_sha256']:
        raise ValueError('Protocol checksum mismatch')
    x = frame[manifest['feature_names']].to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError('Nonfinite features')
    predictions = []
    for filename,checksum in manifest['components'].items():
        if Path(filename).name != filename:
            raise ValueError('Invalid component filename')
        path = directory/filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('Model checksum mismatch')
        if filename == 'frozen_xgboost_small.json':
            import xgboost as xgb
            booster = xgb.Booster()
            booster.load_model(path)
            p = booster.predict(xgb.DMatrix(x,feature_names=manifest['feature_names']))
        else:
            state = json.loads(path.read_text())
            if state['features'] != manifest['feature_names'] or state['classes'] != [0,1,2]:
                raise ValueError('Model feature or class identity mismatch')
            z = (x-np.asarray(state['scaler_mean']))/np.asarray(state['scaler_scale'])
            if state['type'] == 'balanced_logistic':
                p = _softmax(z@np.asarray(state['coef']).T+np.asarray(state['intercept']))
            elif state['type'] == 'mlp_small' and state['activation'] == 'tanh':
                for weight,bias in zip(state['coefs'][:-1],state['intercepts'][:-1]):
                    z = np.tanh(z@np.asarray(weight)+np.asarray(bias))
                p = _softmax(z@np.asarray(state['coefs'][-1])+np.asarray(state['intercepts'][-1]))
            else:
                raise ValueError('Unknown frozen model')
        predictions.append(p)
    p = np.mean(predictions,axis=0)
    if p.shape != (len(frame),3) or not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1):
        raise ValueError('Invalid frozen probabilities')
    return p


def verify():
    import pandas as pd
    from validation.engine_upgrade.train import load_data
    directory = Path(__file__).parent
    report = json.loads((directory/'results.json').read_text())
    data,_,hashes = load_data(pd.Timestamp(report['checked_at']))
    if hashes != report['input_sha256']:
        raise ValueError('Input checksum mismatch')
    test = data[data.day.astype(str).isin(report['test_dates'])]
    replay = json.loads((directory/'replay.json').read_text())
    expected = np.asarray([r['probabilities'][report['selected_on_validation']] for r in replay])
    actual = infer(test)
    if not np.allclose(actual,expected,atol=1e-7):
        raise ValueError('Frozen replay differs from scored model')
    for row,record in zip(test.itertuples(),replay):
        if (row.ticker != record['ticker'] or str(row.ts) != record['candle_start']
                or str(row.feature_asof) != record['feature_asof'] or int(row.label) != record['actual_label']
                or pd.Timestamp(record['training_end']).date() >= row.day
                or not record['historical_replay'] or record['live_forecast']):
            raise ValueError('Replay identity or chronology mismatch')
    return dict(verified_rows=len(test),max_probability_difference=float(np.max(np.abs(actual-expected))),retrained=False,production_enabled=False)


if __name__ == '__main__':
    print(json.dumps(verify()))
