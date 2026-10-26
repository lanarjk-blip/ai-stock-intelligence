"""Offline fixed-budget candidate training; never imported by app or worker.

Validation alone chooses a model. Weights are saved before retrospective test
scoring. Archives were already examined: final scoring cannot prove promotion.
"""
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.quality import conditional_models as cm

HERE = Path(__file__).parent
FEATURES = [f for f in cm.FEATURES if f != 'ticker_bbca']
NAMES = ['balanced_logistic', 'xgboost_small', 'mlp_small', 'equal_ensemble']


def load_data(cutoff):
    protocol = json.loads((HERE/'protocol.json').read_text())
    accepted, coverage, hashes = [], [], {}
    for ticker in protocol['universe']:
        paths = [ROOT/'validation/acceptance/inputs'/f'{ticker}.csv',
                 ROOT/'validation/reliability/inputs'/f'{ticker}.csv',
                 ROOT/'validation/expanded/inputs'/f'{ticker}.csv',
                 ROOT/'validation/expanded/recovered'/f'{ticker}.csv',
                 HERE/f'{ticker}.csv']
        paths = [p for p in paths if p.exists()]
        hashes[ticker] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        raw = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
        features = cm.features(raw, ticker, cutoff)
        enough = features.day.nunique() >= protocol['minimum_days_per_ticker']
        coverage.append(dict(ticker=ticker,rows=len(features),days=features.day.nunique(),included=bool(enough)))
        if enough:
            accepted.append(features)
    return pd.concat(accepted, ignore_index=True).sort_values(['ts','ticker']), coverage, hashes


def models(protocol):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from xgboost import XGBClassifier
    configs = protocol['fixed_configs']
    mlp = dict(configs['mlp_small'])
    mlp['hidden_layer_sizes'] = tuple(mlp['hidden_layer_sizes'])
    return {'balanced_logistic':make_pipeline(StandardScaler(),LogisticRegression(**configs['balanced_logistic'])),
            'xgboost_small':XGBClassifier(**configs['xgboost_small']),
            'mlp_small':make_pipeline(StandardScaler(),MLPClassifier(**mlp))}


def validate_split(train, test):
    if train.empty or test.empty or train.day.max() >= test.day.min():
        raise ValueError('Training must precede the complete test day')
    if not (train.feature_asof <= train.ts).all() or not (test.feature_asof <= test.ts).all():
        raise ValueError('Feature timestamp leakage')
    if set(train.label.unique()) != {0,1,2}:
        raise ValueError('Need all direction classes in training')
    if not np.isfinite(train[FEATURES].to_numpy()).all() or not np.isfinite(test[FEATURES].to_numpy()).all():
        raise ValueError('Nonfinite model features')


def probabilities(model, x):
    values = np.zeros((len(x),3))
    values[:,np.asarray(model.classes_,dtype=int)] = model.predict_proba(x)
    if not np.isfinite(values).all() or (values < 0).any() or not np.allclose(values.sum(axis=1),1):
        raise ValueError('Invalid probability vector')
    return values


def baselines(train, test):
    predictions = {}
    for name in ['global_baseline','slot_baseline']:
        rows = []
        for row in test.itertuples():
            history = train[train.ticker.eq(row.ticker)]
            if name == 'slot_baseline':
                history = history[history.slot.eq(row.slot)]
            counts = np.bincount(history.label,minlength=3)
            rows.append((counts+1)/(counts.sum()+3))
        predictions[name] = np.array(rows)
    return predictions


def fit(train, test, protocol):
    validate_split(train,test)
    trained = models(protocol)
    notices = []
    for name,model in trained.items():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            model.fit(train[FEATURES],train.label)
        notices.extend({'model':name,'message':str(w.message)} for w in caught)
    return trained, notices


def predict(trained, test, train):
    predictions = {name:probabilities(model,test[FEATURES]) for name,model in trained.items()}
    predictions['equal_ensemble'] = np.mean(list(predictions.values()),axis=0)
    predictions.update(baselines(train,test))
    return predictions


def choose(validation):
    return min(NAMES,key=lambda name:(validation[name]['brier'],name))


def freeze(trained, selected, train, protocol):
    """Portable JSON weights, saved before computing final test probabilities."""
    components = list(trained) if selected == 'equal_ensemble' else [selected]
    files = {}
    for name in components:
        model = trained[name]
        path = HERE/f'frozen_{name}.json'
        if name == 'xgboost_small':
            model.save_model(path)
        else:
            scale,estimator = model.steps[0][1],model.steps[-1][1]
            state = dict(type=name,features=FEATURES,classes=estimator.classes_.tolist(),
                         scaler_mean=scale.mean_.tolist(),scaler_scale=scale.scale_.tolist())
            if name == 'mlp_small':
                state.update(coefs=[v.tolist() for v in estimator.coefs_],intercepts=[v.tolist() for v in estimator.intercepts_],activation=estimator.activation)
            else:
                state.update(coef=estimator.coef_.tolist(),intercept=estimator.intercept_.tolist())
            path.write_text(json.dumps(state,indent=2))
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = dict(selected_on_validation=selected,training_end=str(train.day.max()),
                    feature_names=FEATURES,class_order=['DOWN','DATAR','UP'],components=files,
                    protocol_sha256=hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
                    production_enabled=False,promotion='BLOCKED: no independent prospective evaluation')
    (HERE/'frozen_candidate.json').write_text(json.dumps(manifest,indent=2))
    return manifest


def promotion_decision(independent, intervals, coverage_ok):
    # Even a positive archive result is never independently verified evidence.
    if not independent:
        return 'NO PROMOTION: retrospective archives are not independent prospective evidence'
    if not coverage_ok or any(low <= 0 for low,high in intervals.values()):
        return 'NO PROMOTION: coverage or baseline comparison failed'
    return 'REVIEW ONLY: prospective evidence still requires cost/risk and per-ticker checks'


def run():
    import sklearn,xgboost
    started = time.perf_counter()
    protocol = json.loads((HERE/'protocol.json').read_text())
    data,coverage,hashes = load_data(pd.Timestamp.now(tz='UTC'))
    warmup,validation_days,test_days = cm.split_days(data)
    labels,validation_predictions,folds,notices = [], {name:[] for name in NAMES+['global_baseline','slot_baseline']}, [], []
    for day in validation_days:
        train,test = data[data.day<day],data[data.day.eq(day)]
        trained,ws = fit(train,test,protocol)
        predictions = predict(trained,test,train)
        notices.extend(ws)
        labels.extend(test.label.tolist())
        folds.append(dict(training_end=str(train.day.max()),validation_date=str(day),train_n=len(train),validation_n=len(test)))
        for name,p in predictions.items():
            validation_predictions[name].extend(p.tolist())
    validation = {name:cm.metrics(labels,np.array(p)) for name,p in validation_predictions.items()}
    selected = choose(validation)
    train,test = data[data.day<test_days[0]],data[data.day.isin(test_days)]
    trained,ws = fit(train,test,protocol)
    notices.extend(ws)
    frozen = freeze(trained,selected,train,protocol)
    predictions = predict(trained,test,train)
    metrics = {name:cm.metrics(test.label,p) for name,p in predictions.items()}
    actual = np.eye(3)[test.label.to_numpy()]
    losses = {name:((p-actual)**2).sum(axis=1) for name,p in predictions.items()}
    intervals = {}
    for name in ['global_baseline','slot_baseline']:
        gains = pd.DataFrame({'day':test.day.to_numpy(),'gain':losses[name]-losses[selected]}).groupby('day').gain.mean().to_numpy()
        bootstrap = np.random.default_rng(91).choice(gains,size=(2000,len(gains)),replace=True).mean(axis=1)
        intervals[name] = np.quantile(bootstrap,[.025,.975]).tolist()
    report = dict(checked_at=pd.Timestamp.now(tz='Asia/Jakarta').isoformat(),production_enabled=False,
                  decision=promotion_decision(False,intervals,all(c['included'] for c in coverage)),
                  limitations=protocol['costs']+'; archives previously examined; no synchronized IHSG/sector inputs',
                  protocol_sha256=frozen['protocol_sha256'],input_sha256=hashes,coverage=coverage,
                  versions=dict(sklearn=sklearn.__version__,xgboost=xgboost.__version__,pandas=pd.__version__,numpy=np.__version__),
                  features=FEATURES,validation=validation,selected_on_validation=selected,
                  training_end=str(train.day.max()),test_dates=[str(d) for d in test_days],test=metrics,
                  per_ticker={t:{name:cm.metrics(test.loc[test.ticker.eq(t),'label'],p[test.ticker.eq(t).to_numpy()]) for name,p in predictions.items()} for t in sorted(test.ticker.unique())},
                  brier_gain_day_bootstrap95=intervals,folds=folds,warnings=notices,
                  elapsed_seconds=round(time.perf_counter()-started,3))
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    replay = [dict(ticker=row.ticker,candle_start=str(row.ts),feature_asof=str(row.feature_asof),
                   training_end=frozen['training_end'],actual_label=int(row.label),historical_replay=True,live_forecast=False,
                   probabilities={name:p[i].tolist() for name,p in predictions.items()}) for i,row in enumerate(test.itertuples())]
    (HERE/'replay.json').write_text(json.dumps(replay,indent=2))
    print(json.dumps({k:report[k] for k in ['decision','coverage','selected_on_validation','test_dates','brier_gain_day_bootstrap95','elapsed_seconds']},indent=2))
    print(json.dumps({name:{k:m[k] for k in ['n','directional_accuracy','balanced_accuracy','precision_up','precision_down','brier','ece']} for name,m in metrics.items()},indent=2))


if __name__ == '__main__':
    run()
