"""R68-R73 maturity corrections for the final adaptive engine.

This module deliberately patches the already-stable R61-R67 layer instead of
replacing it. Corrections:
- preserve global chronological order in temporal samples;
- use non-overlapping expanding-window OOS folds;
- require stricter promotion evidence before production activation;
- use expected-value execution edge with upside AND stop-loss downside;
- keep runtime regime labels consistent with the same IHSG regime classifier
  used during training;
- publish the actual production model name/validation metric to the UI;
- force one fresh post-maturity retrain so stale pre-R72 artifacts cannot be
  promoted using the older validation geometry.

The invariant remains conservative: this layer may never promote a weaker
baseline signal into a stronger trade.
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

import app
import advanced_ensemble as ae

REVISION = "R68-R73-MATURITY-TRUTH-1"
_INSTALLED = False
_ORIGINAL_HARDEN = None
_ORIGINAL_TRAIN_FINAL = None


def temporal_matrix_chronological(rows, lags=ae.TEMP_LAGS):
    """Lag by ticker while returning usable rows in global chronological order.

    R61-R67 first sorted by ticker/date, which made the subsequent walk-forward
    split segment tickers rather than time. That is not a valid market OOS split.
    Here every ticker is lagged independently, but the returned sample order is
    date/ticker chronological so every expanding-window fold is genuinely later
    than its training data.
    """
    if rows is None or rows.empty:
        return np.empty((0, len(ae.TEMP_FEATURES) * lags)), np.array([], dtype=int)
    d = rows.sort_values(["date", "ticker"]).copy()
    blocks = []
    for lag in range(int(lags)):
        shifted = d.groupby("ticker", sort=False)[ae.TEMP_FEATURES].shift(lag)
        shifted.columns = [f"{c}_lag{lag}" for c in ae.TEMP_FEATURES]
        blocks.append(shifted)
    x = pd.concat(blocks, axis=1)
    values = x.to_numpy(dtype=float)
    ok = np.isfinite(values).all(axis=1)
    return values[ok], d.index.to_numpy()[ok]


def walk_forward_non_overlapping(n, folds=4):
    """Expanding train window with disjoint chronological test windows."""
    n = int(n)
    folds = max(2, int(folds))
    if n < 900:
        return []
    first_train = max(600, int(n * 0.55))
    remaining = n - first_train
    if remaining < folds * 80:
        folds = max(2, remaining // 80)
    if folds < 2:
        return []
    step = max(80, remaining // folds)
    out = []
    train_end = first_train
    for i in range(folds):
        test_start = train_end
        test_end = n if i == folds - 1 else min(n, test_start + step)
        if test_end - test_start < 80:
            break
        out.append((train_end, test_end))
        train_end = test_end
    return out


def execution_cost_expected_value(row, latest, p_up):
    """Conservative net edge after fees, spread, slippage and downside risk."""
    fee = float(app.CFGD.get("advanced_round_trip_fee_pct", 0.0035))
    atr = max(0.0, float(latest.get("atr_pct", 0) or 0))
    turnover = max(0.0, float(latest.get("median_turnover_20", 0) or 0))

    spread = min(0.0075, 0.0006 + 0.08 * atr)
    slip = min(0.0120, 0.0005 + 0.10 * atr)
    if turnover < 500_000_000:
        slip += 0.0060
    elif turnover < 1_000_000_000:
        slip += 0.0040
    elif turnover < 5_000_000_000:
        slip += 0.0015
    total_cost = fee + spread + slip

    def _num(name, fallback=None):
        try:
            v = row.get(name, fallback)
            if v is None or pd.isna(v):
                return fallback
            return float(v)
        except Exception:
            return fallback

    close = _num("close", float(latest.get("close", 0) or 0)) or 0.0
    entry = _num("entry_high", close) or close
    tp1 = _num("target_1", None)
    stop = _num("stop_loss", None)

    if entry <= 0:
        entry = close
    upside = max(0.0, tp1 / entry - 1.0) if tp1 and entry > 0 else max(0.0, 1.5 * atr)
    downside = max(0.0, 1.0 - stop / entry) if stop and entry > 0 else max(0.005, atr)

    p_up = float(np.clip(float(p_up), 0.0, 1.0))
    p_down_base = _num("jst_p_down", None)
    if p_down_base is None:
        p_down = max(0.0, 1.0 - p_up)
    else:
        p_down = float(np.clip(p_down_base, 0.0, max(0.0, 1.0 - p_up)))

    gross_edge = p_up * upside - p_down * downside
    net_edge = gross_edge - total_cost
    rr = upside / max(downside, 1e-6)
    liquidity_penalty = 1.0 if turnover <= 0 else float(np.clip(5_000_000_000 / max(turnover, 1.0), 0.0, 1.0))
    execution_risk = float(np.clip(
        0.55 * (total_cost / max(0.005, upside if upside > 0 else 0.005))
        + 0.25 * liquidity_penalty
        + 0.20 * (1.0 if rr < 1.25 else 0.0),
        0.0, 1.0,
    ))
    return {
        "fee_pct": fee,
        "spread_pct": spread,
        "slippage_pct": slip,
        "estimated_cost_pct": total_cost,
        "upside_pct": upside,
        "downside_pct": downside,
        "risk_reward": rr,
        "gross_edge_pct": gross_edge,
        "net_edge_pct": net_edge,
        "execution_risk": execution_risk,
    }


def _current_regime():
    try:
        con = app.dbcon()
        try:
            ih = app.load_ihsg_daily(con)
        finally:
            con.close()
        if ih is None or ih.empty:
            return None
        r = ih.iloc[-1]
        return ae._regime_from_values(r.get("ihsg_ma20_gap", 0), r.get("ihsg_vol20", 0))
    except Exception:
        return None


def harden_rows_consistent_regime(res, bundle=None):
    if res is None or res.empty:
        return res
    d = res.copy()
    regime = _current_regime()
    if regime:
        d["market_regime"] = str(regime)
    return _ORIGINAL_HARDEN(d, bundle=bundle)


def _training_payload(result):
    """Resolve complete validation metadata even when train_final returns SKIP_FRESH."""
    payload = dict(result or {}) if isinstance(result, dict) else {}
    if "production_enabled" not in payload:
        try:
            bundle = ae.load_bundle()
            meta = (bundle or {}).get("meta") or {}
            for key, value in meta.items():
                payload.setdefault(key, value)
        except Exception:
            pass
    return payload


def _publish_model_truth(result):
    """Keep dashboard model identity and WF metric aligned with production reality."""
    payload = _training_payload(result)
    enabled = bool(payload.get("production_enabled"))
    status = str(payload.get("status") or "").upper()
    if status == "ACTIVE":
        enabled = True

    try:
        if enabled:
            winner = str(payload.get("temporal_winner") or "TEMPORAL").replace("_", " ")
            app.state_set("active_model_name", f"ADAPTIVE ENSEMBLE • JST + XGBOOST + {winner}")
            app.state_set("engine_model_status", "ACTIVE_ENSEMBLE")
            metrics = payload.get("ensemble_metrics") or {}
            wf = metrics.get("accuracy")
            try:
                wf = float(wf)
                if np.isfinite(wf) and 0.0 <= wf <= 1.0:
                    app.state_set("walk_forward_accuracy", wf)
                    app.state_set("walk_forward_source", "ADVANCED_ENSEMBLE_OOS")
            except Exception:
                pass
        else:
            # Baseline training runs immediately before the advanced challenger,
            # so its JST walk-forward value is already the truthful production metric.
            app.state_set("active_model_name", "JST")
            app.state_set("engine_model_status", "JST_WITH_ENSEMBLE_SHADOW")
            app.state_set("walk_forward_source", "JST_BASELINE")

        app.state_set("advanced_ensemble_runtime_status", "ACTIVE" if enabled else "SHADOW_ONLY")
        app.state_set("runtime_guard_revision", REVISION)
        app.state_set("engine_version", str(app.CFGD.get("engine_version", "1.8.57")))
        if os.environ.get("AI_ENGINE_WORKER") == "1":
            app.state_set(
                "local_worker_runtime_build",
                f"V{app.CFGD.get('engine_version', '1.8.57')}-R73",
            )
    except Exception:
        pass
    return payload


def train_final_truth(universe, force=False):
    """Run a fresh matured validation once, then publish only the earned winner."""
    needs_maturity_retrain = str(app.state_get("advanced_maturity_model_revision") or "") != REVISION
    result = _ORIGINAL_TRAIN_FINAL(universe, force=bool(force or needs_maturity_retrain))
    payload = _publish_model_truth(result)
    if payload.get("trained_at") or str(payload.get("status") or "").upper() in {"ACTIVE", "SHADOW_ONLY"}:
        try:
            app.state_set("advanced_maturity_model_revision", REVISION)
        except Exception:
            pass
    return result


def install():
    global _INSTALLED, _ORIGINAL_HARDEN, _ORIGINAL_TRAIN_FINAL
    if _INSTALLED:
        return True

    # Promotion must be earned. No negative calibration concession in production.
    app.CFGD["advanced_ensemble_min_rows"] = max(3000, int(app.CFGD.get("advanced_ensemble_min_rows", 0) or 0))
    app.CFGD["advanced_walk_forward_folds"] = max(4, int(app.CFGD.get("advanced_walk_forward_folds", 0) or 0))
    app.CFGD["advanced_min_composite_gain"] = max(0.003, float(app.CFGD.get("advanced_min_composite_gain", 0.0) or 0.0))
    app.CFGD["advanced_min_brier_gain"] = max(0.0, float(app.CFGD.get("advanced_min_brier_gain", 0.0) or 0.0))
    app.CFGD["advanced_min_up_precision"] = max(0.50, float(app.CFGD.get("advanced_min_up_precision", 0.0) or 0.0))
    app.CFGD["advanced_min_net_edge_pct"] = max(0.0020, float(app.CFGD.get("advanced_min_net_edge_pct", 0.0) or 0.0))
    app.CFGD["advanced_buy_probability_min"] = max(0.60, float(app.CFGD.get("advanced_buy_probability_min", 0.0) or 0.0))

    ae._temporal_matrix = temporal_matrix_chronological
    ae._walk_forward_slices = walk_forward_non_overlapping
    ae._cost_model = execution_cost_expected_value
    _ORIGINAL_HARDEN = ae.harden_rows
    ae.harden_rows = harden_rows_consistent_regime
    _ORIGINAL_TRAIN_FINAL = ae.train_final
    ae.train_final = train_final_truth

    try:
        app.state_set("advanced_maturity_revision", REVISION)
        app.state_set("runtime_guard_revision", REVISION)
        if os.environ.get("AI_ENGINE_WORKER") == "1":
            app.state_set(
                "local_worker_runtime_build",
                f"V{app.CFGD.get('engine_version', '1.8.57')}-R73",
            )
    except Exception:
        pass
    _INSTALLED = True
    return True


def status_snapshot():
    return {
        "revision": REVISION,
        "installed": bool(_INSTALLED),
        "min_rows": app.CFGD.get("advanced_ensemble_min_rows"),
        "walk_forward_folds": app.CFGD.get("advanced_walk_forward_folds"),
        "min_composite_gain": app.CFGD.get("advanced_min_composite_gain"),
        "min_brier_gain": app.CFGD.get("advanced_min_brier_gain"),
        "min_up_precision": app.CFGD.get("advanced_min_up_precision"),
        "min_net_edge_pct": app.CFGD.get("advanced_min_net_edge_pct"),
        "runtime_build": f"V{app.CFGD.get('engine_version', '1.8.57')}-R73",
    }
