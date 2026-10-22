"""R61-R67 final adaptive decision engine.

This layer closes the remaining production gaps above R55-R60:
- real XGBoost tabular challenger (optional import, fail-open);
- two temporal challengers trained on leakage-safe lagged sequences;
- walk-forward OOS model selection;
- regime-aware dynamic meta weights;
- probability calibration;
- execution-cost / liquidity / slippage gate;
- shadow-before-production and a strict never-promote invariant.

It patches app.train_jst/analyze_all *after* ensemble_safety, so the proven
R50-R60 path remains the baseline and this layer may only preserve or reduce
aggressiveness unless its own OOS validation earns production status.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import app

try:
    from xgboost import XGBClassifier
except Exception:  # fail-open on constrained Termux/runtime environments
    XGBClassifier = None

REVISION = "R61-R67-FINAL-7OF7-1"
META_PATH = os.path.join(app.DB_DIR, "advanced_ensemble_meta.json")
NPZ_PATH = os.path.join(app.DB_DIR, "advanced_ensemble_temporal.npz")
XGB_PATH = os.path.join(app.DB_DIR, "advanced_ensemble_xgb.json")

TEMP_FEATURES = [
    "ret1", "ret3", "ret5", "ret10", "rsi14", "ma20_gap", "ma50_gap",
    "macd_gap", "atr_pct", "vol_ratio", "range_pct", "trend_strength",
]
TEMP_LAGS = 5
_CACHE = {"stamp": None, "bundle": None}

_DECISION_RANK = {"WAIT": 0, "SELL/AVOID": 0, "WATCH": 1, "NEAR ENTRY": 2, "BUY": 3}


def _never_promote(old, new):
    old = str(old or "WAIT").upper()
    new = str(new or old).upper()
    return new if _DECISION_RANK.get(new, 0) <= _DECISION_RANK.get(old, 0) else old


def _softmax(z):
    z = np.asarray(z, float)
    if z.ndim == 1:
        z = z.reshape(1, -1)
    z = z - np.nanmax(z, axis=1, keepdims=True)
    e = np.exp(np.clip(z, -50, 50))
    s = e.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    return e / s


def _brier(y, p):
    y = np.asarray(y, int)
    p = np.asarray(p, float)
    if not len(y) or p.shape != (len(y), 3):
        return float("nan")
    one = np.zeros_like(p)
    one[np.arange(len(y)), np.clip(y, 0, 2)] = 1.0
    return float(np.mean(np.sum((p - one) ** 2, axis=1)))


def _metrics(y, p):
    y = np.asarray(y, int)
    p = np.asarray(p, float)
    raw = app.classification_metrics(y, p.argmax(1), p)
    return {
        "accuracy": float(raw.get("accuracy", np.nan)),
        "balanced_accuracy": float(raw.get("balanced_accuracy", np.nan)),
        "macro_f1": float(raw.get("macro_f1", np.nan)),
        "up_precision": float(raw.get("up_precision", np.nan)),
        "up_recall": float(raw.get("up_recall", np.nan)),
        "up_f1": float(raw.get("up_f1", np.nan)),
        "brier": _brier(y, p),
    }


def _composite(m):
    parts = [(0.40, m.get("balanced_accuracy")), (0.35, m.get("up_precision")), (0.25, m.get("macro_f1"))]
    score = weight = 0.0
    for w, v in parts:
        try:
            v = float(v)
            if np.isfinite(v):
                score += w * v
                weight += w
        except Exception:
            pass
    return score / weight if weight else 0.0


def _regime_from_values(ma_gap, vol):
    try:
        vol = float(vol)
    except Exception:
        vol = 0.0
    try:
        ma_gap = float(ma_gap)
    except Exception:
        ma_gap = 0.0
    if np.isfinite(vol) and vol > 0.018:
        return "HIGH_VOLATILITY"
    if ma_gap > 0.005:
        return "BULLISH"
    if ma_gap < -0.005:
        return "BEARISH"
    return "SIDEWAYS"


def _regime_series(df):
    return pd.Series([
        _regime_from_values(a, v)
        for a, v in zip(
            pd.to_numeric(df.get("ihsg_ma20_gap", 0), errors="coerce").fillna(0),
            pd.to_numeric(df.get("ihsg_vol20", 0), errors="coerce").fillna(0),
        )
    ], index=df.index)


def _temporal_matrix(rows, lags=TEMP_LAGS):
    """Create leakage-safe lag matrix using only observations available on/before row date."""
    if rows is None or rows.empty:
        return np.empty((0, len(TEMP_FEATURES) * lags)), np.array([], dtype=int)
    d = rows.sort_values(["ticker", "date"]).copy()
    blocks = []
    for lag in range(lags):
        shifted = d.groupby("ticker", sort=False)[TEMP_FEATURES].shift(lag)
        shifted.columns = [f"{c}_lag{lag}" for c in TEMP_FEATURES]
        blocks.append(shifted)
    x = pd.concat(blocks, axis=1)
    ok = np.isfinite(x.to_numpy(dtype=float)).all(axis=1)
    return x.to_numpy(dtype=float)[ok], d.index.to_numpy()[ok]


def _period_transform(X, lags=TEMP_LAGS, n_features=len(TEMP_FEATURES)):
    X = np.asarray(X, float)
    if X.ndim != 2 or X.shape[1] != lags * n_features:
        return np.empty((len(X), n_features * 4))
    seq = X.reshape(len(X), lags, n_features)
    last = seq[:, 0, :]
    mean = np.nanmean(seq, axis=1)
    slope = seq[:, 0, :] - seq[:, -1, :]
    fft = np.abs(np.fft.rfft(seq, axis=1))
    periodic = fft[:, 1, :] if fft.shape[1] > 1 else np.zeros_like(last)
    return np.concatenate([last, mean, slope, periodic], axis=1)


def _fit_temporal_mlp(X, y, seed):
    m = app.NumpyJST(X.shape[1], h=32, c=3, seed=1400 + seed)
    cw = app.inverse_frequency_weights(y)
    m.fit(X, y, epochs=10, lr=0.008, batch=512, class_weights=cw)
    return m


def _fit_period_model(X, y, seed):
    Z = _period_transform(X)
    m = app.NumpyLogistic(Z.shape[1], 3, seed=1700 + seed)
    m.fit(Z, y, epochs=45, lr=0.025, batch=1024, class_weights=app.inverse_frequency_weights(y))
    return m


def _fit_xgb(X, y, seed):
    if XGBClassifier is None:
        return None
    cw = app.inverse_frequency_weights(y)
    sample_weight = cw[np.asarray(y, int)]
    m = XGBClassifier(
        objective="multi:softprob", num_class=3,
        n_estimators=int(app.CFGD.get("advanced_xgb_estimators", 90)),
        max_depth=int(app.CFGD.get("advanced_xgb_max_depth", 3)),
        learning_rate=float(app.CFGD.get("advanced_xgb_learning_rate", 0.05)),
        subsample=0.85, colsample_bytree=0.85,
        reg_lambda=2.0, reg_alpha=0.05,
        tree_method="hist", n_jobs=max(1, min(4, int(app.CFGD.get("collector_workers", 4)))),
        random_state=2100 + seed, eval_metric="mlogloss",
    )
    m.fit(X, y, sample_weight=sample_weight, verbose=False)
    return m


def _fit_jst(X, y, seed):
    m = app.NumpyJST(X.shape[1], app.CFGD.get("jst_hidden", 24), seed=1100 + seed)
    m.fit(X, y, max(8, int(app.CFGD.get("jst_epochs", 10))), float(app.CFGD.get("jst_learning_rate", .01)), int(app.CFGD.get("jst_batch_size", 512)), class_weights=app.inverse_frequency_weights(y))
    return m


def _walk_forward_slices(n, folds=3):
    folds = max(2, int(folds))
    out = []
    for i in range(folds):
        train_frac = 0.52 + 0.10 * i
        test_frac = train_frac + 0.12
        a, b = int(n * train_frac), min(n, int(n * test_frac))
        if a >= 500 and b - a >= 100:
            out.append((a, b))
    return out


def _weights_from_metrics(metrics):
    raw = {k: max(0.01, _composite(v)) for k, v in metrics.items()}
    s = sum(raw.values()) or 1.0
    return {k: float(v / s) for k, v in raw.items()}


def _blend(preds, weights):
    names = [k for k in weights if k in preds and preds[k] is not None]
    if not names:
        return None
    w = np.asarray([max(0.0, float(weights[k])) for k in names], float)
    if not np.isfinite(w).all() or w.sum() <= 0:
        w = np.ones(len(names))
    w /= w.sum()
    p = np.zeros_like(np.asarray(preds[names[0]], float))
    for wi, name in zip(w, names):
        p += wi * np.asarray(preds[name], float)
    p = np.clip(p, 1e-9, 1.0)
    return p / p.sum(axis=1, keepdims=True)


def _calibration_alpha(y, p, priors):
    priors = np.asarray(priors, float).reshape(1, 3)
    best_a, best = 1.0, _brier(y, p)
    for a in np.linspace(0.50, 1.0, 11):
        q = a * p + (1 - a) * priors
        q /= q.sum(axis=1, keepdims=True)
        b = _brier(y, q)
        if np.isfinite(b) and (not np.isfinite(best) or b < best):
            best_a, best = float(a), float(b)
    return best_a, best


def train_final(universe, force=False):
    if universe is None or getattr(universe, "empty", True):
        return {"status": "SKIP_EMPTY"}
    if XGBClassifier is None:
        app.state_set("advanced_ensemble_status", "XGBOOST_UNAVAILABLE")
        return {"status": "XGBOOST_UNAVAILABLE"}
    last = app.state_get("advanced_ensemble_trained_at")
    hours = float(app.CFGD.get("advanced_ensemble_retrain_hours", 24))
    if not force and last and os.path.exists(META_PATH) and os.path.exists(NPZ_PATH) and os.path.exists(XGB_PATH):
        try:
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() < hours * 3600:
                return {"status": "SKIP_FRESH"}
        except Exception:
            pass

    con = app.dbcon()
    try:
        tr = app.build_training_rows(con, universe.ticker.astype(str).tolist(), horizon=int(app.CFGD.get("primary_target_horizon", 3)))
    finally:
        con.close()
    min_rows = int(app.CFGD.get("advanced_ensemble_min_rows", 1600))
    if len(tr) < min_rows:
        app.state_set("advanced_ensemble_status", f"INSUFFICIENT_DATA:{len(tr)}")
        return {"status": "INSUFFICIENT_DATA", "rows": int(len(tr))}
    tr = tr.sort_values(["date", "ticker"]).reset_index(drop=True)
    max_rows = int(app.CFGD.get("advanced_ensemble_max_rows", 50000))
    if len(tr) > max_rows:
        tr = tr.iloc[-max_rows:].reset_index(drop=True)

    Xt_all, idx = _temporal_matrix(tr)
    if len(idx) < min_rows // 2:
        return {"status": "TEMPORAL_DATA_INSUFFICIENT", "rows": int(len(idx))}
    base = tr.loc[idx].copy().reset_index(drop=True)
    Xt_all, _ = _temporal_matrix(tr.loc[idx].reset_index(drop=True)) if False else (Xt_all, idx)
    X_all = base[app.FEATURES].to_numpy(float)
    y_all = base.y.to_numpy(int)
    regimes = _regime_series(base).to_numpy()

    oos = {"JST": [], "XGBOOST": [], "TEMPORAL_MLP": [], "TEMPORAL_PERIOD": []}
    oos_y, oos_reg = [], []
    for fold, (a, b) in enumerate(_walk_forward_slices(len(base), int(app.CFGD.get("advanced_walk_forward_folds", 3))), start=1):
        Xtr, Xte = X_all[:a], X_all[a:b]
        Ttr, Tte = Xt_all[:a], Xt_all[a:b]
        ytr, yte = y_all[:a], y_all[a:b]
        models = {
            "JST": _fit_jst(Xtr, ytr, fold),
            "XGBOOST": _fit_xgb(Xtr, ytr, fold),
            "TEMPORAL_MLP": _fit_temporal_mlp(Ttr, ytr, fold),
            "TEMPORAL_PERIOD": _fit_period_model(Ttr, ytr, fold),
        }
        oos["JST"].append(models["JST"].predict_proba(Xte))
        oos["XGBOOST"].append(models["XGBOOST"].predict_proba(Xte))
        oos["TEMPORAL_MLP"].append(models["TEMPORAL_MLP"].predict_proba(Tte))
        oos["TEMPORAL_PERIOD"].append(models["TEMPORAL_PERIOD"].predict_proba(_period_transform(Tte)))
        oos_y.append(yte); oos_reg.append(regimes[a:b])
    if not oos_y:
        return {"status": "NO_WALK_FORWARD_FOLDS"}
    yv = np.concatenate(oos_y); rv = np.concatenate(oos_reg)
    preds = {k: np.concatenate(v, axis=0) for k, v in oos.items()}
    metrics = {k: _metrics(yv, p) for k, p in preds.items()}
    temporal_winner = max(("TEMPORAL_MLP", "TEMPORAL_PERIOD"), key=lambda n: _composite(metrics[n]))
    selected = ["JST", "XGBOOST", temporal_winner]

    global_metrics = {k: metrics[k] for k in selected}
    global_weights = _weights_from_metrics(global_metrics)
    regime_weights = {}
    for regime in ("BULLISH", "SIDEWAYS", "BEARISH", "HIGH_VOLATILITY"):
        mask = rv == regime
        if int(mask.sum()) < 80:
            regime_weights[regime] = dict(global_weights)
            continue
        rm = {k: _metrics(yv[mask], preds[k][mask]) for k in selected}
        regime_weights[regime] = _weights_from_metrics(rm)

    blended = np.zeros((len(yv), 3), float)
    for regime in np.unique(rv):
        mask = rv == regime
        w = regime_weights.get(str(regime), global_weights)
        q = _blend({k: preds[k][mask] for k in selected}, w)
        blended[mask] = q
    priors = (np.bincount(y_all, minlength=3).astype(float) + 1.0)
    priors /= priors.sum()
    alpha, _ = _calibration_alpha(yv, blended, priors)
    calibrated = alpha * blended + (1 - alpha) * priors.reshape(1, 3)
    calibrated /= calibrated.sum(axis=1, keepdims=True)
    ensemble_metrics = _metrics(yv, calibrated)
    best_single = max(selected, key=lambda n: _composite(metrics[n]))
    comp_gain = _composite(ensemble_metrics) - _composite(metrics[best_single])
    brier_gain = float(metrics[best_single]["brier"] - ensemble_metrics["brier"])
    enabled = bool(
        comp_gain >= float(app.CFGD.get("advanced_min_composite_gain", 0.0))
        and brier_gain >= float(app.CFGD.get("advanced_min_brier_gain", -0.003))
        and float(ensemble_metrics.get("up_precision", 0.0)) >= float(app.CFGD.get("advanced_min_up_precision", 0.34))
    )

    # Refit production models on all available rows.
    jst = _fit_jst(X_all, y_all, 99)
    xgb = _fit_xgb(X_all, y_all, 99)
    temporal_mlp = _fit_temporal_mlp(Xt_all, y_all, 99)
    temporal_period = _fit_period_model(Xt_all, y_all, 99)
    xgb.save_model(XGB_PATH)
    arrays = {
        "priors": priors,
        "jst_W1": jst.W1, "jst_b1": jst.b1, "jst_W2": jst.W2, "jst_b2": jst.b2, "jst_mu": jst.mu, "jst_sd": jst.sd,
        "tmlp_W1": temporal_mlp.W1, "tmlp_b1": temporal_mlp.b1, "tmlp_W2": temporal_mlp.W2, "tmlp_b2": temporal_mlp.b2, "tmlp_mu": temporal_mlp.mu, "tmlp_sd": temporal_mlp.sd,
        "tperiod_W": temporal_period.W, "tperiod_b": temporal_period.b, "tperiod_mu": temporal_period.mu, "tperiod_sd": temporal_period.sd,
    }
    np.savez(NPZ_PATH, **arrays)
    meta = {
        "revision": REVISION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(base)),
        "walk_forward_samples": int(len(yv)),
        "temporal_winner": temporal_winner,
        "selected_models": selected,
        "global_weights": global_weights,
        "regime_weights": regime_weights,
        "calibration_alpha": float(alpha),
        "metrics": metrics,
        "ensemble_metrics": ensemble_metrics,
        "best_single": best_single,
        "composite_gain": float(comp_gain),
        "brier_gain": float(brier_gain),
        "production_enabled": enabled,
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    for k, v in {
        "advanced_ensemble_trained_at": meta["trained_at"],
        "advanced_ensemble_status": "ACTIVE" if enabled else "SHADOW_ONLY",
        "advanced_ensemble_temporal_winner": temporal_winner,
        "advanced_ensemble_comp_gain": comp_gain,
        "advanced_ensemble_brier_gain": brier_gain,
    }.items():
        app.state_set(k, v)
    _CACHE["stamp"] = None; _CACHE["bundle"] = None
    return {"status": "ACTIVE" if enabled else "SHADOW_ONLY", **meta}


def load_bundle():
    try:
        if not (os.path.exists(META_PATH) and os.path.exists(NPZ_PATH) and os.path.exists(XGB_PATH)) or XGBClassifier is None:
            return None
        stamp = max(os.path.getmtime(META_PATH), os.path.getmtime(NPZ_PATH), os.path.getmtime(XGB_PATH))
        if _CACHE["bundle"] is not None and _CACHE["stamp"] == stamp:
            return _CACHE["bundle"]
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        z = np.load(NPZ_PATH)
        jst = app.NumpyJST(len(app.FEATURES), app.CFGD.get("jst_hidden", 24))
        for k in ("W1", "b1", "W2", "b2", "mu", "sd"):
            setattr(jst, k, np.asarray(z[f"jst_{k}"]))
        tmlp = app.NumpyJST(len(TEMP_FEATURES) * TEMP_LAGS, 32)
        for k in ("W1", "b1", "W2", "b2", "mu", "sd"):
            setattr(tmlp, k, np.asarray(z[f"tmlp_{k}"]))
        pmodel = app.NumpyLogistic(len(TEMP_FEATURES) * 4, 3)
        pmodel.W = np.asarray(z["tperiod_W"]); pmodel.b = np.asarray(z["tperiod_b"])
        pmodel.mu = np.asarray(z["tperiod_mu"]); pmodel.sd = np.asarray(z["tperiod_sd"])
        xgb = XGBClassifier(); xgb.load_model(XGB_PATH)
        bundle = {"meta": meta, "priors": np.asarray(z["priors"], float), "jst": jst, "xgb": xgb, "tmlp": tmlp, "tperiod": pmodel}
        _CACHE.update({"stamp": stamp, "bundle": bundle})
        return bundle
    except Exception:
        return None


def _latest_vectors(ticker):
    con = app.dbcon()
    try:
        df = pd.read_sql_query("SELECT date,open,high,low,close,volume FROM prices WHERE ticker=? ORDER BY date", con, params=(str(ticker),))
        ih = app.load_ihsg_daily(con)
    finally:
        con.close()
    if len(df) < 70:
        return None, None, {}
    z = app.indicators(df).dropna(subset=TEMP_FEATURES)
    if len(z) < TEMP_LAGS:
        return None, None, {}
    seq = z[TEMP_FEATURES].tail(TEMP_LAGS).iloc[::-1].to_numpy(float).reshape(1, -1)
    r = z.iloc[-1]
    ih_latest = ih.iloc[-1].to_dict() if ih is not None and not ih.empty else {}
    feat = dict(r)
    i1, i5, i10 = [float(ih_latest.get(k, 0) or 0) for k in ("ihsg_ret1", "ihsg_ret5", "ihsg_ret10")]
    feat.update({
        "ihsg_ret1": i1, "ihsg_ret5": i5, "ihsg_ret10": i10,
        "ihsg_ma20_gap": float(ih_latest.get("ihsg_ma20_gap", 0) or 0),
        "ihsg_vol20": float(ih_latest.get("ihsg_vol20", 0) or 0),
        "rel_ret1": float(r.ret1) - i1, "rel_ret5": float(r.ret5) - i5, "rel_ret10": float(r.ret10) - i10,
    })
    base = np.asarray([[feat[k] for k in app.FEATURES]], float)
    if not np.isfinite(base).all() or not np.isfinite(seq).all():
        return None, None, {}
    turnover = (pd.to_numeric(df.close, errors="coerce") * pd.to_numeric(df.volume, errors="coerce")).tail(20)
    latest = {
        "close": float(r.close), "atr_pct": float(r.atr_pct) if pd.notna(r.atr_pct) else 0.0,
        "median_turnover_20": float(turnover.median()) if turnover.notna().any() else 0.0,
        "volume": float(r.volume) if pd.notna(r.volume) else 0.0,
    }
    return base, seq, latest


def _cost_model(row, latest, p_up):
    fee = float(app.CFGD.get("advanced_round_trip_fee_pct", 0.0035))
    atr = max(0.0, float(latest.get("atr_pct", 0) or 0))
    turnover = max(0.0, float(latest.get("median_turnover_20", 0) or 0))
    spread = min(0.006, 0.0006 + 0.08 * atr)
    slip = min(0.010, 0.0005 + 0.10 * atr)
    if turnover < 1_000_000_000:
        slip += 0.004
    elif turnover < 5_000_000_000:
        slip += 0.0015
    total_cost = fee + spread + slip
    close = float(row.get("close") or latest.get("close") or 0)
    tp1 = row.get("target_1")
    try:
        upside = max(0.0, float(tp1) / close - 1.0) if close > 0 and pd.notna(tp1) else max(0.0, 1.5 * atr)
    except Exception:
        upside = max(0.0, 1.5 * atr)
    gross_edge = max(0.0, (2.0 * float(p_up) - 1.0)) * upside
    net_edge = gross_edge - total_cost
    risk = float(np.clip(total_cost / max(0.005, upside if upside > 0 else 0.005), 0.0, 1.0))
    return {"fee_pct": fee, "spread_pct": spread, "slippage_pct": slip, "estimated_cost_pct": total_cost, "gross_edge_pct": gross_edge, "net_edge_pct": net_edge, "execution_risk": risk}


def harden_rows(res, bundle=None):
    if res is None or res.empty:
        return res
    out = res.copy()
    bundle = bundle or load_bundle()
    defaults = {
        "advanced_p_up": np.nan, "advanced_confidence": np.nan, "advanced_model_disagreement": np.nan,
        "advanced_status": "UNAVAILABLE", "advanced_temporal_winner": "", "advanced_net_edge_pct": np.nan,
        "advanced_estimated_cost_pct": np.nan, "advanced_execution_risk": np.nan, "advanced_trade_gate": "BASELINE",
    }
    for c, v in defaults.items():
        if c not in out.columns: out[c] = v
    if not bundle:
        return out
    meta = bundle["meta"]; enabled = bool(meta.get("production_enabled")); alpha = float(meta.get("calibration_alpha", 1.0)); priors = bundle["priors"]
    winner = str(meta.get("temporal_winner") or "TEMPORAL_MLP")
    pool = int(app.CFGD.get("advanced_candidate_pool", 80))
    rank = out["relative_rank_score"].rank(ascending=False, method="first") if "relative_rank_score" in out.columns else out["ai_score"].rank(ascending=False, method="first")
    decisions = out.get("decision", pd.Series("WAIT", index=out.index)).astype(str)
    targets = out.index[(rank <= pool) | decisions.isin(["BUY", "NEAR ENTRY", "WATCH"])]
    for idx in targets:
        row = out.loc[idx]
        base, temporal, latest = _latest_vectors(row.get("ticker"))
        if base is None:
            out.at[idx, "advanced_status"] = "LIMITED_DATA"; continue
        regime = str(row.get("market_regime") or "SIDEWAYS").upper()
        weights = (meta.get("regime_weights") or {}).get(regime) or meta.get("global_weights") or {}
        preds = {
            "JST": bundle["jst"].predict_proba(base),
            "XGBOOST": bundle["xgb"].predict_proba(base),
        }
        if winner == "TEMPORAL_PERIOD":
            preds[winner] = bundle["tperiod"].predict_proba(_period_transform(temporal))
        else:
            preds[winner] = bundle["tmlp"].predict_proba(temporal)
        p = _blend(preds, weights)
        if p is None: continue
        p = alpha * p + (1 - alpha) * priors.reshape(1, 3); p /= p.sum(axis=1, keepdims=True)
        p = p[0]
        ups = [float(v[0, 2]) for v in preds.values()]
        disagreement = float(np.std(ups))
        cost = _cost_model(row, latest, float(p[2]))
        out.at[idx, "advanced_p_up"] = float(p[2]); out.at[idx, "advanced_confidence"] = float(np.max(p))
        out.at[idx, "advanced_model_disagreement"] = disagreement; out.at[idx, "advanced_temporal_winner"] = winner
        out.at[idx, "advanced_net_edge_pct"] = cost["net_edge_pct"]; out.at[idx, "advanced_estimated_cost_pct"] = cost["estimated_cost_pct"]
        out.at[idx, "advanced_execution_risk"] = cost["execution_risk"]; out.at[idx, "advanced_status"] = "ACTIVE" if enabled else "SHADOW_ONLY"
        if not enabled: continue

        old = str(row.get("decision") or "WAIT").upper(); new = old; gate = "UNCHANGED"
        severe_dis = disagreement >= float(app.CFGD.get("advanced_disagreement_severe", 0.15))
        weak_edge = cost["net_edge_pct"] < float(app.CFGD.get("advanced_min_net_edge_pct", 0.0015))
        very_weak_edge = cost["net_edge_pct"] < 0.0
        if old == "BUY" and (severe_dis or very_weak_edge or p[2] < float(app.CFGD.get("advanced_buy_probability_min", 0.59))):
            new, gate = "WAIT", "NO_TRADE_FINAL"
        elif old == "BUY" and (weak_edge or disagreement >= float(app.CFGD.get("advanced_disagreement_warn", 0.09))):
            new, gate = "NEAR ENTRY", "FINAL_CAUTION"
        elif old == "NEAR ENTRY" and (very_weak_edge or severe_dis or p[2] < 0.51):
            new, gate = "WAIT", "NO_TRADE_FINAL"
        elif old == "WATCH" and (very_weak_edge and severe_dis):
            new, gate = "WAIT", "NO_TRADE_FINAL"
        new = _never_promote(old, new)
        out.at[idx, "decision"] = new; out.at[idx, "advanced_trade_gate"] = gate
        # compatibility: downstream readers continue to use jst_* as the final calibrated probability only after production earns it.
        out.at[idx, "jst_p_down"] = float(p[0]); out.at[idx, "jst_p_sideways"] = float(p[1]); out.at[idx, "jst_p_up"] = float(p[2])
        out.at[idx, "jst_confidence"] = float(np.max(p)); out.at[idx, "jst_signal"] = app.CLASS_NAMES[int(np.argmax(p))]
        penalty = max(0.0, disagreement - 0.05) * 40.0 + max(0.0, -cost["net_edge_pct"]) * 1000.0 + cost["execution_risk"] * 8.0
        for col in ("ai_score", "decision_score"):
            if col in out.columns:
                try:
                    old_score = float(row.get(col)); out.at[idx, col] = max(0.0, min(old_score, old_score - penalty))
                except Exception: pass
    try:
        app.state_set("advanced_ensemble_runtime_status", "ACTIVE" if enabled else "SHADOW_ONLY")
        app.state_set("advanced_ensemble_last_applied_at", datetime.now(timezone.utc).isoformat())
    except Exception: pass
    return out


def install():
    if getattr(app, "_advanced_ensemble_installed", False): return True
    original_train = getattr(app, "train_jst", None); original_analyze = getattr(app, "analyze_all", None)
    if not callable(original_train) or not callable(original_analyze): return False
    def train_wrapper(universe, *args, **kwargs):
        result = original_train(universe, *args, **kwargs)
        try:
            if isinstance(result, tuple) and result and result[0] is not None: train_final(universe)
        except Exception as exc:
            try: app.state_set("advanced_ensemble_training_error", str(exc)[:800])
            except Exception: pass
        return result
    def analyze_wrapper(universe, model=None, *args, **kwargs):
        result = original_analyze(universe, model, *args, **kwargs)
        try: return harden_rows(result)
        except Exception as exc:
            try: app.state_set("advanced_ensemble_apply_error", str(exc)[:800])
            except Exception: pass
            return result
    app.train_jst = train_wrapper; app.analyze_all = analyze_wrapper; app._advanced_ensemble_installed = True
    return True


def status_snapshot():
    b = load_bundle()
    if not b: return {"revision": REVISION, "status": "UNAVAILABLE"}
    m = b["meta"]
    return {k: m.get(k) for k in ("revision", "trained_at", "temporal_winner", "selected_models", "ensemble_metrics", "best_single", "composite_gain", "brier_gain", "production_enabled")}
