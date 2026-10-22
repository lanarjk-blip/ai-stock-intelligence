import pandas as pd
import numpy as np

import capital_preservation as cp
import risk_hardening as rh


def test_capital_cache_is_scoped_to_database_identity(monkeypatch):
    cp._CACHE.update({"at": 10**9, "metrics": {"ready": True, "loss_streak": 5}, "db_key": "old.db"})
    monkeypatch.setattr(cp, "_db_identity", lambda: "new.db")
    monkeypatch.setattr(cp, "_dbcon", lambda: (_ for _ in ()).throw(RuntimeError("no table")))
    m = cp._recent_returns(force=False)
    assert m["ready"] is False
    assert m["samples"] == 0


def test_crowding_penalises_only_when_correlation_has_enough_evidence():
    n = 50
    a = np.linspace(-0.02, 0.02, n)
    returns = pd.DataFrame({
        "AAA": a,
        "BBB": a * 0.98 + 0.0001,
        "CCC": np.sin(np.arange(n)) * 0.01,
    })
    frame = pd.DataFrame([
        {"ticker": "AAA", "opportunity_score": 90, "state": "ENTRY READY"},
        {"ticker": "BBB", "opportunity_score": 85, "state": "ENTRY READY"},
        {"ticker": "CCC", "opportunity_score": 80, "state": "ENTRY READY"},
    ])
    info = rh.crowding_adjustments(frame, returns=returns)
    assert info["AAA"]["penalty"] == 0.0
    assert info["BBB"]["penalty"] < 0.0
    assert info["BBB"]["max_corr"] > 0.9
    assert info["CCC"]["penalty"] == 0.0


def test_hardening_never_promotes_or_increases_score():
    n = 50
    a = np.linspace(-0.02, 0.02, n)
    returns = pd.DataFrame({"AAA": a, "BBB": a * 0.99})
    frame = pd.DataFrame([
        {"ticker": "AAA", "opportunity_score": 90.0, "state": "ENTRY READY", "confidence": 0.8, "close": 100, "stop_loss": 96, "risk": "LOW"},
        {"ticker": "BBB", "opportunity_score": 88.0, "state": "ENTRY READY", "confidence": 0.8, "close": 100, "stop_loss": 96, "risk": "LOW"},
    ])
    out = rh.harden_frame(
        frame,
        returns=returns,
        capital_metrics={"ready": False},
        degradation={"ready": False, "degraded": False, "severe": False},
    )
    assert (out["opportunity_score"] <= frame["opportunity_score"]).all()
    assert out.iloc[0]["state"] == "ENTRY READY"
    assert out.iloc[1]["state"] in {"DEVELOPING", "MONITOR"}
    assert ((out["risk_size_multiplier"] >= 0.20) & (out["risk_size_multiplier"] <= 1.0)).all()


def test_adaptive_sizing_reduces_risk_for_wider_stop_and_drawdown():
    safe = {"confidence": 0.8, "close": 100, "stop_loss": 97, "risk": "LOW"}
    risky = {"confidence": 0.8, "close": 100, "stop_loss": 90, "risk": "HIGH"}
    normal = rh.adaptive_size_multiplier(safe, capital_metrics={"ready": False}, degradation={"ready": False})
    reduced = rh.adaptive_size_multiplier(
        risky,
        capital_metrics={"ready": True, "loss_streak": 4, "max_drawdown": -9},
        degradation={"ready": True, "degraded": True, "severe": False},
    )
    assert 0.20 <= reduced < normal <= 1.0


def test_walk_forward_degradation_is_relative_not_magic_accuracy_threshold():
    stable = {"folds": [
        {"accuracy": 0.42}, {"accuracy": 0.43}, {"accuracy": 0.42}, {"accuracy": 0.43}
    ]}
    falling = {"folds": [
        {"accuracy": 0.58}, {"accuracy": 0.57}, {"accuracy": 0.47}, {"accuracy": 0.46}
    ]}
    s = rh.validation_degradation(stable)
    f = rh.validation_degradation(falling)
    assert s["ready"] is True and s["degraded"] is False
    assert f["ready"] is True and f["degraded"] is True
    assert f["delta"] < 0


def test_insufficient_validation_folds_fail_open():
    m = rh.validation_degradation({"folds": [{"accuracy": 0.9}, {"accuracy": 0.1}]})
    assert m["ready"] is False
    assert m["degraded"] is False
