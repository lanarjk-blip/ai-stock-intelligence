import numpy as np
import pandas as pd

import app
import ensemble_safety as es


def test_boosted_stumps_probability_shape_and_sum():
    rng = np.random.default_rng(123)
    X = rng.normal(size=(240, 6))
    y = np.where(X[:, 0] > 0.5, 2, np.where(X[:, 1] < -0.5, 0, 1))
    m = es.NumpyBoostedStumps(rounds=3, learning_rate=0.2).fit(X, y)
    p = m.predict_proba(X[:17])
    assert p.shape == (17, 3)
    assert np.all(np.isfinite(p))
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-8)
    assert np.all((p >= 0.0) & (p <= 1.0))


def test_never_promotes_decision():
    assert es._never_promote("WATCH", "BUY") == "WATCH"
    assert es._never_promote("NEAR ENTRY", "BUY") == "NEAR ENTRY"
    assert es._never_promote("BUY", "NEAR ENTRY") == "NEAR ENTRY"
    assert es._never_promote("BUY", "WAIT") == "WAIT"


def test_selective_gate_downgrades_severe_and_warn():
    severe, reason = es._harden_decision("BUY", 0.70, disagreement=0.20, ood=0.05, execution=0.10)
    assert severe == "WAIT"
    assert reason == "NO_TRADE_GUARD"

    warn, reason = es._harden_decision("BUY", 0.62, disagreement=0.11, ood=0.05, execution=0.10)
    assert warn == "NEAR ENTRY"
    assert reason == "ENSEMBLE_CAUTION"


def test_ood_score_is_low_near_training_and_high_for_extreme():
    mu = np.zeros(8)
    sd = np.ones(8)
    assert es._ood_score(np.zeros(8), mu, sd) < 0.01
    assert es._ood_score(np.full(8, 8.0), mu, sd) > 0.50


def _fake_bundle(enabled=True):
    class FixedModel:
        def __init__(self, p):
            self.p = np.asarray(p, dtype=float)

        def predict_proba(self, X):
            return np.repeat(self.p.reshape(1, 3), len(X), axis=0)

    return {
        "meta": {
            "production_enabled": enabled,
            "weights": {"A": 0.34, "B": 0.33, "C": 0.33},
            "calibration_alpha": 1.0,
        },
        "models": {
            "A": FixedModel([0.05, 0.05, 0.90]),
            "B": FixedModel([0.40, 0.30, 0.30]),
            "C": FixedModel([0.45, 0.35, 0.20]),
        },
        "ood_mu": np.zeros(len(app.FEATURES)),
        "ood_sd": np.ones(len(app.FEATURES)),
        "priors": np.array([0.33, 0.34, 0.33]),
    }


def test_hardening_never_increases_score_or_decision(monkeypatch):
    row = {
        "ticker": "TEST",
        "decision": "BUY",
        "decision_score": 88.0,
        "ai_score": 84.0,
        "relative_rank_score": 90.0,
        "jst_p_down": 0.05,
        "jst_p_sideways": 0.10,
        "jst_p_up": 0.85,
        "jst_confidence": 0.85,
        "jst_signal": "UP",
    }
    df = pd.DataFrame([row])

    monkeypatch.setattr(
        es,
        "_latest_feature_vector",
        lambda *args, **kwargs: (
            np.zeros(len(app.FEATURES)),
            {"atr_pct": 0.02, "median_turnover_20": 8_000_000_000, "vol_ratio": 1.0},
        ),
    )
    monkeypatch.setattr(app, "state_set", lambda *args, **kwargs: None)

    class DummyCon:
        def close(self):
            pass

    monkeypatch.setattr(app, "dbcon", lambda: DummyCon())
    monkeypatch.setattr(app, "load_ihsg_daily", lambda con: pd.DataFrame())

    out = es.harden_rows(df, bundle=_fake_bundle(enabled=True), candidate_pool=5)
    assert len(out) == 1
    assert float(out.iloc[0].ai_score) <= 84.0
    assert float(out.iloc[0].decision_score) <= 88.0
    assert es._DECISION_RANK[str(out.iloc[0].decision)] <= es._DECISION_RANK["BUY"]
    assert str(out.iloc[0].decision) != "BUY"
    assert str(out.iloc[0].selective_trade_gate) in {"NO_TRADE_GUARD", "ENSEMBLE_CAUTION"}


def test_shadow_bundle_does_not_change_production_decision(monkeypatch):
    df = pd.DataFrame([
        {
            "ticker": "TEST",
            "decision": "BUY",
            "decision_score": 81.0,
            "ai_score": 79.0,
            "relative_rank_score": 82.0,
            "jst_p_down": 0.05,
            "jst_p_sideways": 0.10,
            "jst_p_up": 0.85,
            "jst_confidence": 0.85,
            "jst_signal": "UP",
        }
    ])
    monkeypatch.setattr(
        es,
        "_latest_feature_vector",
        lambda *args, **kwargs: (
            np.zeros(len(app.FEATURES)),
            {"atr_pct": 0.02, "median_turnover_20": 8_000_000_000, "vol_ratio": 1.0},
        ),
    )
    monkeypatch.setattr(app, "state_set", lambda *args, **kwargs: None)

    class DummyCon:
        def close(self):
            pass

    monkeypatch.setattr(app, "dbcon", lambda: DummyCon())
    monkeypatch.setattr(app, "load_ihsg_daily", lambda con: pd.DataFrame())

    out = es.harden_rows(df, bundle=_fake_bundle(enabled=False), candidate_pool=5)
    assert str(out.iloc[0].decision) == "BUY"
    assert float(out.iloc[0].ai_score) == 79.0
    assert str(out.iloc[0].ensemble_status) == "SHADOW_ONLY"
