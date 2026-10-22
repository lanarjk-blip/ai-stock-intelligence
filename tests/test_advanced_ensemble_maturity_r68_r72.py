import numpy as np
import pandas as pd

import app
import advanced_ensemble as ae
import advanced_ensemble_maturity as maturity


maturity.install()


def _row(ticker, day, base):
    row = {"ticker": ticker, "date": f"2026-01-{day:02d}", "y": day % 3}
    for j, name in enumerate(ae.TEMP_FEATURES):
        row[name] = float(base + day + j)
    return row


def test_temporal_samples_remain_global_chronological_after_lagging():
    # Deliberately interleave tickers by date. The usable output must stay date ordered,
    # not ticker-block ordered, or walk-forward validation would leak future market dates.
    rows = []
    for day in range(1, 9):
        rows.append(_row("AAA", day, 0))
        rows.append(_row("BBB", day, 100))
    df = pd.DataFrame(rows)
    X, idx = maturity.temporal_matrix_chronological(df, lags=5)
    dates = pd.to_datetime(df.loc[idx, "date"]).to_numpy()
    assert len(X) == len(idx) == 8
    assert np.all(dates[:-1] <= dates[1:])
    # On first usable date both tickers must appear before moving to the next date.
    first_dates = df.loc[idx[:2], "date"].tolist()
    assert first_dates == ["2026-01-05", "2026-01-05"]


def test_walk_forward_test_windows_are_disjoint_and_expand_forward():
    folds = maturity.walk_forward_non_overlapping(5000, folds=4)
    assert len(folds) == 4
    previous_test_end = None
    for train_end, test_end in folds:
        assert train_end >= 600
        assert test_end > train_end
        if previous_test_end is not None:
            assert train_end == previous_test_end
        previous_test_end = test_end
    assert folds[-1][1] == 5000


def test_execution_expected_value_penalizes_stop_downside():
    latest = {"atr_pct": 0.02, "median_turnover_20": 20_000_000_000, "close": 100.0}
    tight = {"close": 100.0, "entry_high": 100.0, "target_1": 106.0, "stop_loss": 98.0, "jst_p_down": 0.25}
    wide = {"close": 100.0, "entry_high": 100.0, "target_1": 106.0, "stop_loss": 90.0, "jst_p_down": 0.25}
    a = maturity.execution_cost_expected_value(tight, latest, 0.60)
    b = maturity.execution_cost_expected_value(wide, latest, 0.60)
    assert b["downside_pct"] > a["downside_pct"]
    assert b["net_edge_pct"] < a["net_edge_pct"]
    assert b["execution_risk"] >= a["execution_risk"]


def test_maturity_promotion_thresholds_are_strict():
    snap = maturity.status_snapshot()
    assert snap["installed"] is True
    assert snap["min_rows"] >= 3000
    assert snap["walk_forward_folds"] >= 4
    assert snap["min_composite_gain"] >= 0.003
    assert snap["min_brier_gain"] >= 0.0
    assert snap["min_up_precision"] >= 0.50
    assert snap["min_net_edge_pct"] >= 0.002
    assert snap["runtime_build"].endswith("-R73")


def test_maturity_patch_preserves_never_promote_rule():
    assert ae._never_promote("WAIT", "BUY") == "WAIT"
    assert ae._never_promote("WATCH", "NEAR ENTRY") == "WATCH"
    assert ae._never_promote("BUY", "WAIT") == "WAIT"


def test_active_ensemble_publishes_truthful_dashboard_identity(monkeypatch):
    states = {}
    monkeypatch.setattr(app, "state_set", lambda key, value: states.__setitem__(key, str(value)))
    monkeypatch.setattr(ae, "load_bundle", lambda: None)
    maturity._publish_model_truth({
        "status": "ACTIVE",
        "production_enabled": True,
        "temporal_winner": "TEMPORAL_MLP",
        "ensemble_metrics": {"accuracy": 0.6123},
    })
    assert states["active_model_name"].startswith("ADAPTIVE ENSEMBLE")
    assert "XGBOOST" in states["active_model_name"]
    assert abs(float(states["walk_forward_accuracy"]) - 0.6123) < 1e-12
    assert states["walk_forward_source"] == "ADVANCED_ENSEMBLE_OOS"
    assert states["engine_model_status"] == "ACTIVE_ENSEMBLE"


def test_shadow_ensemble_keeps_jst_as_production_identity(monkeypatch):
    states = {}
    monkeypatch.setattr(app, "state_set", lambda key, value: states.__setitem__(key, str(value)))
    monkeypatch.setattr(ae, "load_bundle", lambda: None)
    maturity._publish_model_truth({"status": "SHADOW_ONLY", "production_enabled": False})
    assert states["active_model_name"] == "JST"
    assert states["engine_model_status"] == "JST_WITH_ENSEMBLE_SHADOW"
    assert states["walk_forward_source"] == "JST_BASELINE"
    assert "walk_forward_accuracy" not in states
