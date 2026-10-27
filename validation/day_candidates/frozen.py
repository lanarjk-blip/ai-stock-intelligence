"""Read-only inference for the quarantined candidate; no fitting or network."""
import hashlib,json
from pathlib import Path
import numpy as np

HERE=Path(__file__).parent


def infer(frame,directory=None):
    root=Path(directory) if directory else HERE
    manifest=json.loads((root/'frozen.json').read_text())
    if hashlib.sha256((root/'protocol.json').read_bytes()).hexdigest()!=manifest['protocol_sha256']:
        raise ValueError('Protocol checksum mismatch')
    x=frame[manifest['features']].to_numpy(dtype=float)
    if not np.isfinite(x).all():raise ValueError('Nonfinite features')
    predictions=[]
    for filename,digest in manifest['components'].items():
        if Path(filename).name!=filename:raise ValueError('Bad model path')
        path=root/filename
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Model checksum mismatch')
        if filename=='frozen_xgboost.json':
            import xgboost as xgb
            model=xgb.Booster();model.load_model(path)
            p=model.predict(xgb.DMatrix(x,feature_names=manifest['features']))
        else:
            state=json.loads(path.read_text())
            if state['classes']!=[0,1,2] or state['features']!=manifest['features']:raise ValueError('Feature/class identity mismatch')
            z=(x-np.asarray(state['mean']))/np.asarray(state['scale'])
            if state['type']=='logistic':z=z@np.asarray(state['coef']).T+np.asarray(state['intercept'])
            elif state['type']=='mlp':
                for w,b in zip(state['coefs'][:-1],state['intercepts'][:-1]):z=np.tanh(z@np.asarray(w)+np.asarray(b))
                z=z@np.asarray(state['coefs'][-1])+np.asarray(state['intercepts'][-1])
            else:raise ValueError('Unknown classifier')
            exp=np.exp(z-z.max(axis=1,keepdims=True));p=exp/exp.sum(axis=1,keepdims=True)
        predictions.append(p)
    alpha=manifest['uniform_mix_alpha']
    p=alpha*np.mean(predictions,axis=0)+(1-alpha)/3
    if p.shape!=(len(frame),3) or not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1):
        raise ValueError('Invalid output')
    return p
