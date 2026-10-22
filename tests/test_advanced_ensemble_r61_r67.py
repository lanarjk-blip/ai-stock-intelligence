import numpy as np
import pandas as pd

import advanced_ensemble as ae


def test_temporal_matrix_is_leakage_safe_shape():
    rows=[]
    for ticker in ('AAA','BBB'):
        for i in range(8):
            row={'ticker':ticker,'date':f'2026-01-{i+1:02d}','y':i%3}
            for j,name in enumerate(ae.TEMP_FEATURES):
                row[name]=float(i+j+1)
            rows.append(row)
    df=pd.DataFrame(rows)
    X,idx=ae._temporal_matrix(df,lags=5)
    assert X.shape[1] == len(ae.TEMP_FEATURES)*5
    assert len(X) == len(idx) == 8
    # First usable AAA row is day 5 and must contain current then past only.
    first=X[0].reshape(5,len(ae.TEMP_FEATURES))
    assert first[0,0] == 5.0
    assert first[-1,0] == 1.0


def test_period_transform_shape_and_finite():
    X=np.arange(3*5*len(ae.TEMP_FEATURES),dtype=float).reshape(3,-1)+1
    Z=ae._period_transform(X)
    assert Z.shape == (3,len(ae.TEMP_FEATURES)*4)
    assert np.isfinite(Z).all()


def test_regime_classifier_contract():
    assert ae._regime_from_values(.02,.01) == 'BULLISH'
    assert ae._regime_from_values(-.02,.01) == 'BEARISH'
    assert ae._regime_from_values(0,.01) == 'SIDEWAYS'
    assert ae._regime_from_values(.02,.03) == 'HIGH_VOLATILITY'


def test_meta_weights_are_normalized():
    m={
        'JST':{'balanced_accuracy':.50,'up_precision':.55,'macro_f1':.48},
        'XGBOOST':{'balanced_accuracy':.57,'up_precision':.61,'macro_f1':.55},
        'TEMPORAL_MLP':{'balanced_accuracy':.53,'up_precision':.58,'macro_f1':.52},
    }
    w=ae._weights_from_metrics(m)
    assert abs(sum(w.values())-1.0) < 1e-9
    assert w['XGBOOST'] > w['JST']


def test_never_promote_invariant():
    assert ae._never_promote('WAIT','BUY') == 'WAIT'
    assert ae._never_promote('WATCH','BUY') == 'WATCH'
    assert ae._never_promote('BUY','WAIT') == 'WAIT'
    assert ae._never_promote('BUY','NEAR ENTRY') == 'NEAR ENTRY'


def test_cost_model_penalizes_low_liquidity():
    row={'close':100.0,'target_1':105.0}
    liquid=ae._cost_model(row,{'atr_pct':.02,'median_turnover_20':20_000_000_000},.65)
    illiquid=ae._cost_model(row,{'atr_pct':.02,'median_turnover_20':500_000_000},.65)
    assert illiquid['estimated_cost_pct'] > liquid['estimated_cost_pct']
    assert illiquid['net_edge_pct'] < liquid['net_edge_pct']


def test_xgboost_dependency_is_real_when_installed():
    # requirements.txt now makes this mandatory in production/CI; keep the test
    # explicit so a packaging regression cannot silently downgrade to a fake tree model.
    assert ae.XGBClassifier is not None
