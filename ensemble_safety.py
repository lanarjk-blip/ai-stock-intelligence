"""R55-R60 adaptive ensemble safety layer.

Design goals:
- keep the deployed model as the baseline authority;
- train lightweight dependency-free challengers on the same leakage-safe rows;
- only let the ensemble reduce aggressiveness unless validation proves it;
- fail open when artifacts/data are missing;
- surface model disagreement, OOD and execution-quality diagnostics.

This module deliberately avoids TensorFlow/PyTorch/XGBoost runtime dependencies so
the production worker remains small enough for the current Render/Android stack.
The tree challenger is a compact multiclass gradient-boosted stump model.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import app

REVISION = "R55-R60-ADAPTIVE-ENSEMBLE-1"
NPZ_PATH = os.path.join(app.DB_DIR, "ensemble_safety_v1.npz")
META_PATH = os.path.join(app.DB_DIR, "ensemble_safety_meta.json")

_CACHE = {"mtime": None, "bundle": None}


def _softmax(z):
    z = np.asarray(z, dtype=float)
    if z.ndim == 1:
        z = z.reshape(1, -1)
    z = z - np.nanmax(z, axis=1, keepdims=True)
    e = np.exp(np.clip(z, -50, 50))
    s = e.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    return e / s


def _brier_multiclass(y, p, classes=3):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    if len(y) == 0 or p.ndim != 2 or p.shape[0] != len(y):
        return float("nan")
    one = np.zeros((len(y), classes), dtype=float)
    one[np.arange(len(y)), np.clip(y, 0, classes - 1)] = 1.0
    return float(np.mean(np.sum((p - one) ** 2, axis=1)))


def _metric_pack(y, p):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    if not len(y):
        return {
            "accuracy": float("nan"),
            "balanced_accuracy": float("nan"),
            "macro_f1": float("nan"),
            "up_precision": float("nan"),
            "up_recall": float("nan"),
            "up_f1": float("nan"),
            "brier": float("nan"),
        }
    yp = p.argmax(1)
    raw = app.classification_metrics(y, yp, p)
    out = {}
    for key in ("accuracy", "balanced_accuracy", "macro_f1", "up_precision", "up_recall", "up_f1"):
        value = raw.get(key)
        out[key] = float(value) if value is not None else float("nan")
    out["brier"] = _brier_multiclass(y, p)
    return out


class NumpyBoostedStumps:
    """Small multiclass gradient-boosted decision-stump classifier.

    The point is model diversity, not a heavyweight booster. Each round fits
    one residual stump per class and stays dependency-free for production.
    """

    def __init__(self, rounds=12, learning_rate=0.20, quantiles=(0.20, 0.40, 0.60, 0.80)):
        self.rounds = int(rounds)
        self.learning_rate = float(learning_rate)
        self.quantiles = tuple(float(q) for q in quantiles)
        self.base_logits = np.zeros(3, dtype=float)
        self.mu = None
        self.sd = None
        self.stump_class = []
        self.stump_feature = []
        self.stump_threshold = []
        self.stump_left = []
        self.stump_right = []

    def _standardize_fit(self, X):
        X = np.asarray(X, dtype=float)
        self.mu = np.nanmean(X, axis=0)
        self.sd = np.nanstd(X, axis=0)
        self.sd[~np.isfinite(self.sd) | (self.sd < 1e-8)] = 1.0
        out = (X - self.mu) / self.sd
        return np.nan_to_num(out, nan=0.0, posinf=6.0, neginf=-6.0)

    def _standardize(self, X):
        X = np.asarray(X, dtype=float)
        out = (X - self.mu) / self.sd
        return np.nan_to_num(out, nan=0.0, posinf=6.0, neginf=-6.0)

    def fit(self, X, y, class_weights=None):
        Xs = self._standardize_fit(X)
        y = np.asarray(y, dtype=int)
        n, d = Xs.shape
        if n == 0:
            return self
        counts = np.bincount(y, minlength=3).astype(float) + 1.0
        priors = counts / counts.sum()
        self.base_logits = np.log(priors)
        logits = np.repeat(self.base_logits.reshape(1, -1), n, axis=0)
        cw = np.asarray(class_weights if class_weights is not None else [1.0, 1.0, 1.0], dtype=float)
        sample_w = cw[np.clip(y, 0, 2)]
        sample_w = sample_w / max(1e-12, float(np.mean(sample_w)))
        thresholds = []
        for j in range(d):
            vals = Xs[:, j]
            try:
                qs = np.unique(np.quantile(vals, self.quantiles))
            except Exception:
                qs = np.array([0.0])
            thresholds.append(qs[np.isfinite(qs)])

        for _ in range(max(1, self.rounds)):
            p = _softmax(logits)
            one = np.zeros_like(p)
            one[np.arange(n), np.clip(y, 0, 2)] = 1.0
            resid = (one - p) * sample_w[:, None]
            for c in range(3):
                rc = resid[:, c]
                best = None
                best_loss = float("inf")
                for j in range(d):
                    xj = Xs[:, j]
                    for th in thresholds[j]:
                        mask = xj <= th
                        nl = int(mask.sum())
                        nr = n - nl
                        if nl < max(20, n // 100) or nr < max(20, n // 100):
                            continue
                        lv = float(np.mean(rc[mask]))
                        rv = float(np.mean(rc[~mask]))
                        pred = np.where(mask, lv, rv)
                        loss = float(np.mean((rc - pred) ** 2))
                        if loss < best_loss:
                            best_loss = loss
                            best = (j, float(th), lv, rv)
                if best is None:
                    continue
                j, th, lv, rv = best
                step = np.where(Xs[:, j] <= th, lv, rv)
                logits[:, c] += self.learning_rate * step
                self.stump_class.append(int(c))
                self.stump_feature.append(int(j))
                self.stump_threshold.append(float(th))
                self.stump_left.append(float(lv))
                self.stump_right.append(float(rv))
        return self

    def predict_proba(self, X):
        Xs = self._standardize(X)
        n = len(Xs)
        logits = np.repeat(self.base_logits.reshape(1, -1), n, axis=0)
        for c, j, th, lv, rv in zip(
            self.stump_class, self.stump_feature, self.stump_threshold, self.stump_left, self.stump_right
        ):
            logits[:, int(c)] += self.learning_rate * np.where(Xs[:, int(j)] <= th, lv, rv)
        return _softmax(logits)

    def to_arrays(self):
        return {
            "boost_base_logits": np.asarray(self.base_logits, dtype=float),
            "boost_mu": np.asarray(self.mu, dtype=float),
            "boost_sd": np.asarray(self.sd, dtype=float),
            "boost_class": np.asarray(self.stump_class, dtype=int),
            "boost_feature": np.asarray(self.stump_feature, dtype=int),
            "boost_threshold": np.asarray(self.stump_threshold, dtype=float),
            "boost_left": np.asarray(self.stump_left, dtype=float),
            "boost_right": np.asarray(self.stump_right, dtype=float),
            "boost_lr": np.asarray([self.learning_rate], dtype=float),
        }

    @classmethod
    def from_arrays(cls, z):
        obj = cls(rounds=1, learning_rate=float(np.asarray(z["boost_lr"]).ravel()[0]))
        obj.base_logits = np.asarray(z["boost_base_logits"], dtype=float)
        obj.mu = np.asarray(z["boost_mu"], dtype=float)
        obj.sd = np.asarray(z["boost_sd"], dtype=float)
        obj.stump_class = np.asarray(z["boost_class"], dtype=int).tolist()
        obj.stump_feature = np.asarray(z["boost_feature"], dtype=int).tolist()
        obj.stump_threshold = np.asarray(z["boost_threshold"], dtype=float).tolist()
        obj.stump_left = np.asarray(z["boost_left"], dtype=float).tolist()
        obj.stump_right = np.asarray(z["boost_right"], dtype=float).tolist()
        return obj


def _composite_metric(m):
    vals = [
        (0.45, m.get("balanced_accuracy")),
        (0.35, m.get("up_precision")),
        (0.20, m.get("macro_f1")),
    ]
    total = 0.0
    weight = 0.0
    for w, v in vals:
        try:
            v = float(v)
            if np.isfinite(v):
                total += w * v
                weight += w
        except Exception:
            pass
    return total / weight if weight else 0.0


def _fit_models(X, y, seed=1):
    cw = app.inverse_frequency_weights(y)
    jst = app.NumpyJST(len(app.FEATURES), app.CFGD.get("jst_hidden", 24), seed=700 + seed)
    jst.fit(
        X, y,
        max(6, int(app.CFGD.get("jst_epochs", 10))),
        float(app.CFGD.get("jst_learning_rate", 0.01)),
        int(app.CFGD.get("jst_batch_size", 512)),
        class_weights=cw,
    )
    logistic = app.NumpyLogistic(len(app.FEATURES), 3, seed=900 + seed)
    logistic.fit(X, y, epochs=45, lr=0.03, batch=1024, class_weights=cw)
    boost = NumpyBoostedStumps(
        rounds=int(app.CFGD.get("ensemble_boost_rounds", 10)),
        learning_rate=float(app.CFGD.get("ensemble_boost_learning_rate", 0.20)),
    ).fit(X, y, class_weights=cw)
    return {"JST_WEIGHTED": jst, "LOGISTIC_WEIGHTED": logistic, "BOOSTED_STUMPS": boost}


def _choose_calibration(y, p, priors):
    priors = np.asarray(priors, dtype=float).reshape(1, -1)
    best_alpha = 1.0
    best_brier = _brier_multiclass(y, p)
    for alpha in np.linspace(0.55, 1.0, 10):
        q = alpha * p + (1.0 - alpha) * priors
        q = q / q.sum(axis=1, keepdims=True)
        score = _brier_multiclass(y, q)
        if np.isfinite(score) and (not np.isfinite(best_brier) or score < best_brier):
            best_alpha = float(alpha)
            best_brier = float(score)
    return best_alpha, best_brier


def _weighted_average(preds, weights):
    names = [n for n in weights if n in preds]
    if not names:
        return None
    w = np.asarray([max(0.0, float(weights[n])) for n in names], dtype=float)
    if not np.isfinite(w).all() or w.sum() <= 0:
        w = np.ones(len(names), dtype=float)
    w = w / w.sum()
    p = np.zeros_like(np.asarray(preds[names[0]], dtype=float))
    for wi, n in zip(w, names):
        p += wi * np.asarray(preds[n], dtype=float)
    p = np.clip(p, 1e-9, 1.0)
    return p / p.sum(axis=1, keepdims=True)


def train_challengers(universe, force=False):
    """Train/validate challenger ensemble and persist only a conservative bundle."""
    if universe is None or getattr(universe, "empty", True):
        return {"status": "SKIP_EMPTY_UNIVERSE"}
    hours = float(app.CFGD.get("ensemble_retrain_hours", 24))
    last = app.state_get("ensemble_trained_at")
    if not force and last:
        try:
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
            if age < hours and os.path.exists(NPZ_PATH) and os.path.exists(META_PATH):
                return {"status": "SKIP_FRESH", "age_hours": age}
        except Exception:
            pass

    con = app.dbcon()
    try:
        horizon = int(app.CFGD.get("primary_target_horizon", 3))
        tr = app.build_training_rows(con, universe.ticker.astype(str).tolist(), horizon=horizon)
    finally:
        con.close()
    min_rows = int(app.CFGD.get("ensemble_min_training_rows", 1200))
    if len(tr) < min_rows:
        app.state_set("ensemble_status", f"INSUFFICIENT_DATA:{len(tr)}")
        return {"status": "INSUFFICIENT_DATA", "rows": int(len(tr))}
    tr = tr.sort_values(["date", "ticker"]).reset_index(drop=True)
    max_rows = int(app.CFGD.get("ensemble_max_rows", 60000))
    if len(tr) > max_rows:
        tr = tr.iloc[-max_rows:].reset_index(drop=True)

    cut = max(500, int(len(tr) * 0.78))
    if cut >= len(tr) - 100:
        cut = max(500, len(tr) - max(100, len(tr) // 5))
    train = tr.iloc[:cut]
    valid = tr.iloc[cut:]
    Xtr = train[app.FEATURES].values.astype(float)
    ytr = train.y.values.astype(int)
    Xv = valid[app.FEATURES].values.astype(float)
    yv = valid.y.values.astype(int)

    models = _fit_models(Xtr, ytr, seed=1)
    preds = {name: model.predict_proba(Xv) for name, model in models.items()}
    metrics = {name: _metric_pack(yv, p) for name, p in preds.items()}
    raw_weights = {name: max(0.01, _composite_metric(m)) for name, m in metrics.items()}
    total = sum(raw_weights.values()) or 1.0
    weights = {name: float(v / total) for name, v in raw_weights.items()}
    ensemble_raw = _weighted_average(preds, weights)
    counts = np.bincount(ytr, minlength=3).astype(float) + 1.0
    priors = counts / counts.sum()
    alpha, calibrated_brier = _choose_calibration(yv, ensemble_raw, priors)
    ensemble_cal = alpha * ensemble_raw + (1.0 - alpha) * priors.reshape(1, -1)
    ensemble_cal = ensemble_cal / ensemble_cal.sum(axis=1, keepdims=True)
    ensemble_metrics = _metric_pack(yv, ensemble_cal)
    ensemble_metrics["brier"] = calibrated_brier

    best_name = max(metrics, key=lambda n: _composite_metric(metrics[n]))
    best = metrics[best_name]
    comp_gain = _composite_metric(ensemble_metrics) - _composite_metric(best)
    brier_gain = float(best.get("brier", np.nan)) - float(ensemble_metrics.get("brier", np.nan))
    best_up = float(best.get("up_precision", np.nan))
    ens_up = float(ensemble_metrics.get("up_precision", np.nan))
    up_ok = (not np.isfinite(best_up)) or (not np.isfinite(ens_up)) or ens_up >= best_up - 0.02
    enabled = bool(
        up_ok
        and comp_gain >= float(app.CFGD.get("ensemble_min_composite_gain", -0.002))
        and brier_gain >= float(app.CFGD.get("ensemble_min_brier_gain", -0.005))
    )

    Xall = tr[app.FEATURES].values.astype(float)
    yall = tr.y.values.astype(int)
    final_models = _fit_models(Xall, yall, seed=11)
    jst = final_models["JST_WEIGHTED"]
    logistic = final_models["LOGISTIC_WEIGHTED"]
    boost = final_models["BOOSTED_STUMPS"]
    mu = np.nanmean(Xall, axis=0)
    sd = np.nanstd(Xall, axis=0)
    sd[~np.isfinite(sd) | (sd < 1e-8)] = 1.0

    arrays = {
        "jst_W1": jst.W1, "jst_b1": jst.b1, "jst_W2": jst.W2, "jst_b2": jst.b2,
        "jst_mu": jst.mu, "jst_sd": jst.sd,
        "log_W": logistic.W, "log_b": logistic.b, "log_mu": logistic.mu, "log_sd": logistic.sd,
        "ood_mu": mu, "ood_sd": sd, "class_priors": priors,
    }
    arrays.update(boost.to_arrays())
    np.savez(NPZ_PATH, **arrays)
    payload = {
        "revision": REVISION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "horizon_days": horizon,
        "rows": int(len(tr)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(valid)),
        "weights": weights,
        "calibration_alpha": float(alpha),
        "production_enabled": enabled,
        "best_single": best_name,
        "metrics": metrics,
        "ensemble_metrics": ensemble_metrics,
        "composite_gain": float(comp_gain),
        "brier_gain": float(brier_gain),
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    app.state_set("ensemble_trained_at", payload["trained_at"])
    app.state_set("ensemble_status", "ENABLED" if enabled else "SHADOW_ONLY")
    app.state_set("ensemble_best_single", best_name)
    app.state_set("ensemble_validation_composite_gain", comp_gain)
    app.state_set("ensemble_validation_brier_gain", brier_gain)
    _CACHE["mtime"] = None
    _CACHE["bundle"] = None
    return {"status": "ENABLED" if enabled else "SHADOW_ONLY", **payload}


def load_bundle():
    try:
        if not (os.path.exists(NPZ_PATH) and os.path.exists(META_PATH)):
            return None
        mtime = max(os.path.getmtime(NPZ_PATH), os.path.getmtime(META_PATH))
        if _CACHE["bundle"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["bundle"]
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        z = np.load(NPZ_PATH)
        jst = app.NumpyJST(len(app.FEATURES), app.CFGD.get("jst_hidden", 24))
        jst.W1 = np.asarray(z["jst_W1"]); jst.b1 = np.asarray(z["jst_b1"])
        jst.W2 = np.asarray(z["jst_W2"]); jst.b2 = np.asarray(z["jst_b2"])
        jst.mu = np.asarray(z["jst_mu"]); jst.sd = np.asarray(z["jst_sd"])
        logistic = app.NumpyLogistic(len(app.FEATURES), 3)
        logistic.W = np.asarray(z["log_W"]); logistic.b = np.asarray(z["log_b"])
        logistic.mu = np.asarray(z["log_mu"]); logistic.sd = np.asarray(z["log_sd"])
        boost = NumpyBoostedStumps.from_arrays(z)
        bundle = {
            "meta": meta,
            "models": {
                "JST_WEIGHTED": jst,
                "LOGISTIC_WEIGHTED": logistic,
                "BOOSTED_STUMPS": boost,
            },
            "ood_mu": np.asarray(z["ood_mu"], dtype=float),
            "ood_sd": np.asarray(z["ood_sd"], dtype=float),
            "priors": np.asarray(z["class_priors"], dtype=float),
        }
        _CACHE["mtime"] = mtime
        _CACHE["bundle"] = bundle
        return bundle
    except Exception:
        return None


def _latest_feature_vector(ticker, ih_latest=None):
    con = app.dbcon()
    try:
        df = pd.read_sql_query(
            "SELECT date,open,high,low,close,volume FROM prices WHERE ticker=? ORDER BY date",
            con,
            params=(str(ticker),),
        )
        if ih_latest is None:
            ih = app.load_ihsg_daily(con)
            ih_latest = ih.iloc[-1].to_dict() if not ih.empty else {}
    finally:
        con.close()
    if len(df) < 60:
        return None, {}
    z = app.indicators(df)
    r = z.iloc[-1]
    feat = dict(r)
    ihsg_r1 = float(ih_latest.get("ihsg_ret1", 0) or 0)
    ihsg_r5 = float(ih_latest.get("ihsg_ret5", 0) or 0)
    ihsg_r10 = float(ih_latest.get("ihsg_ret10", 0) or 0)
    feat.update({
        "ihsg_ret1": ihsg_r1,
        "ihsg_ret5": ihsg_r5,
        "ihsg_ret10": ihsg_r10,
        "ihsg_ma20_gap": float(ih_latest.get("ihsg_ma20_gap", 0) or 0),
        "ihsg_vol20": float(ih_latest.get("ihsg_vol20", 0) or 0),
        "rel_ret1": float(r.ret1) - ihsg_r1 if pd.notna(r.ret1) else np.nan,
        "rel_ret5": float(r.ret5) - ihsg_r5 if pd.notna(r.ret5) else np.nan,
        "rel_ret10": float(r.ret10) - ihsg_r10 if pd.notna(r.ret10) else np.nan,
    })
    f = np.asarray([feat[k] for k in app.FEATURES], dtype=float)
    if not np.all(np.isfinite(f)):
        return None, {}
    latest = {
        "volume": float(r.volume) if pd.notna(r.volume) else np.nan,
        "close": float(r.close) if pd.notna(r.close) else np.nan,
        "atr_pct": float(r.atr_pct) if pd.notna(r.atr_pct) else np.nan,
        "vol_ratio": float(r.vol_ratio) if pd.notna(r.vol_ratio) else np.nan,
    }
    try:
        turnover = (pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")).tail(20)
        latest["median_turnover_20"] = float(turnover.median()) if turnover.notna().any() else np.nan
    except Exception:
        latest["median_turnover_20"] = np.nan
    return f, latest


def _ood_score(f, mu, sd):
    z = np.abs((np.asarray(f, float) - np.asarray(mu, float)) / np.asarray(sd, float))
    z = np.nan_to_num(z, nan=0.0, posinf=10.0, neginf=10.0)
    extreme_fraction = float(np.mean(z > 3.5))
    tail = float(np.mean(np.clip(z - 2.0, 0.0, 6.0)) / 6.0)
    return float(np.clip(0.65 * extreme_fraction + 0.35 * tail, 0.0, 1.0))


def _execution_risk(latest):
    atr = latest.get("atr_pct")
    turnover = latest.get("median_turnover_20")
    vol_ratio = latest.get("vol_ratio")
    risk = 0.0
    if atr is not None and np.isfinite(atr):
        risk += float(np.clip((float(atr) - 0.025) / 0.06, 0.0, 1.0)) * 0.45
    if turnover is not None and np.isfinite(turnover):
        low = float(app.CFGD.get("ensemble_low_turnover_rp", 1_000_000_000))
        soft = float(app.CFGD.get("ensemble_soft_turnover_rp", 5_000_000_000))
        if turnover < low:
            risk += 0.40
        elif turnover < soft:
            risk += 0.20 * (soft - turnover) / max(1.0, soft - low)
    if vol_ratio is not None and np.isfinite(vol_ratio) and float(vol_ratio) < 0.45:
        risk += 0.15
    return float(np.clip(risk, 0.0, 1.0))


_DECISION_RANK = {"WAIT": 0, "SELL/AVOID": 0, "WATCH": 1, "NEAR ENTRY": 2, "BUY": 3}


def _never_promote(old, new):
    return new if _DECISION_RANK.get(str(new), 0) <= _DECISION_RANK.get(str(old), 0) else old


def _harden_decision(old, p_up, disagreement, ood, execution):
    old = str(old or "WAIT").upper()
    if old not in _DECISION_RANK:
        return old, "UNCHANGED"
    severe = (
        ood >= float(app.CFGD.get("ensemble_ood_severe", 0.30))
        or disagreement >= float(app.CFGD.get("ensemble_disagreement_severe", 0.16))
        or execution >= float(app.CFGD.get("ensemble_execution_severe", 0.70))
    )
    warn = (
        ood >= float(app.CFGD.get("ensemble_ood_warn", 0.16))
        or disagreement >= float(app.CFGD.get("ensemble_disagreement_warn", 0.10))
        or execution >= float(app.CFGD.get("ensemble_execution_warn", 0.45))
    )
    buy_p = float(app.CFGD.get("ensemble_buy_probability_min", 0.58))
    near_p = float(app.CFGD.get("ensemble_near_probability_min", 0.52))
    if old == "BUY":
        if severe or p_up < near_p:
            return "WAIT", "NO_TRADE_GUARD"
        if warn or p_up < buy_p:
            return "NEAR ENTRY", "ENSEMBLE_CAUTION"
    elif old == "NEAR ENTRY":
        if severe or p_up < 0.50:
            return "WAIT", "NO_TRADE_GUARD"
        if warn or p_up < near_p:
            return "WATCH", "ENSEMBLE_CAUTION"
    elif old == "WATCH" and severe:
        return "WAIT", "NO_TRADE_GUARD"
    return old, "UNCHANGED"


def harden_rows(res, bundle=None, candidate_pool=None):
    """Add ensemble diagnostics and only preserve/reduce aggressiveness."""
    if res is None or res.empty:
        return res
    bundle = bundle or load_bundle()
    out = res.copy()
    defaults = {
        "ensemble_p_down": np.nan,
        "ensemble_p_sideways": np.nan,
        "ensemble_p_up": np.nan,
        "ensemble_confidence": np.nan,
        "ensemble_disagreement": np.nan,
        "ensemble_agreement": np.nan,
        "ensemble_ood_score": np.nan,
        "ensemble_execution_risk": np.nan,
        "ensemble_status": "UNAVAILABLE",
        "selective_trade_gate": "BASELINE",
    }
    for c, v in defaults.items():
        if c not in out.columns:
            out[c] = v
    if not bundle:
        return out

    meta = bundle.get("meta", {})
    enabled = bool(meta.get("production_enabled", False))
    weights = meta.get("weights") or {}
    alpha = float(meta.get("calibration_alpha", 1.0))
    priors = np.asarray(bundle.get("priors", [1 / 3, 1 / 3, 1 / 3]), dtype=float)
    pool = int(candidate_pool or app.CFGD.get("ensemble_candidate_pool", 80))
    if "relative_rank_score" in out.columns:
        order = out["relative_rank_score"].rank(ascending=False, method="first")
    elif "ai_score" in out.columns:
        order = out["ai_score"].rank(ascending=False, method="first")
    else:
        order = pd.Series(np.arange(len(out)) + 1, index=out.index)
    decision_series = out["decision"] if "decision" in out.columns else pd.Series("", index=out.index)
    target_idx = out.index[(order <= pool) | decision_series.isin(["BUY", "NEAR ENTRY", "WATCH"])]

    con = app.dbcon()
    try:
        ih = app.load_ihsg_daily(con)
        ih_latest = ih.iloc[-1].to_dict() if not ih.empty else {}
    finally:
        con.close()

    changed = 0
    no_trade = 0
    for idx in target_idx:
        row = out.loc[idx]
        f, latest = _latest_feature_vector(row.get("ticker"), ih_latest=ih_latest)
        if f is None:
            out.at[idx, "ensemble_status"] = "LIMITED_DATA"
            continue
        preds = {}
        for name, model in bundle["models"].items():
            try:
                preds[name] = model.predict_proba(f.reshape(1, -1))
            except Exception:
                pass
        p = _weighted_average(preds, weights)
        if p is None:
            out.at[idx, "ensemble_status"] = "MODEL_ERROR"
            continue
        p = alpha * p + (1.0 - alpha) * priors.reshape(1, -1)
        p = p / p.sum(axis=1, keepdims=True)
        p = p[0]
        model_up = [float(v[0, 2]) for v in preds.values() if np.asarray(v).shape == (1, 3)]
        disagreement = float(np.std(model_up)) if len(model_up) >= 2 else 0.0
        agreement = float(np.clip(1.0 - disagreement / 0.25, 0.0, 1.0))
        ood = _ood_score(f, bundle["ood_mu"], bundle["ood_sd"])
        execution = _execution_risk(latest)

        out.at[idx, "ensemble_p_down"] = float(p[0])
        out.at[idx, "ensemble_p_sideways"] = float(p[1])
        out.at[idx, "ensemble_p_up"] = float(p[2])
        out.at[idx, "ensemble_confidence"] = float(np.max(p))
        out.at[idx, "ensemble_disagreement"] = disagreement
        out.at[idx, "ensemble_agreement"] = agreement
        out.at[idx, "ensemble_ood_score"] = ood
        out.at[idx, "ensemble_execution_risk"] = execution
        out.at[idx, "ensemble_status"] = "ACTIVE" if enabled else "SHADOW_ONLY"

        if not enabled:
            continue

        for src, dst in [
            ("jst_p_down", "base_model_p_down"),
            ("jst_p_sideways", "base_model_p_sideways"),
            ("jst_p_up", "base_model_p_up"),
            ("jst_confidence", "base_model_confidence"),
            ("jst_signal", "base_model_signal"),
        ]:
            if src in out.columns and dst not in out.columns:
                out[dst] = np.nan if src != "jst_signal" else ""
            if src in out.columns:
                out.at[idx, dst] = row.get(src)

        out.at[idx, "jst_p_down"] = float(p[0])
        out.at[idx, "jst_p_sideways"] = float(p[1])
        out.at[idx, "jst_p_up"] = float(p[2])
        out.at[idx, "jst_confidence"] = float(np.max(p))
        out.at[idx, "jst_signal"] = app.CLASS_NAMES[int(np.argmax(p))]

        old_decision = str(row.get("decision") or "WAIT")
        new_decision, gate = _harden_decision(old_decision, float(p[2]), disagreement, ood, execution)
        new_decision = _never_promote(old_decision, new_decision)
        out.at[idx, "decision"] = new_decision
        out.at[idx, "selective_trade_gate"] = gate
        if gate == "NO_TRADE_GUARD":
            no_trade += 1
        if new_decision != old_decision:
            changed += 1

        penalty = max(0.0, disagreement - 0.06) * 45.0 + ood * 18.0 + execution * 12.0
        try:
            old_score = float(row.get("ai_score"))
            out.at[idx, "ai_score"] = float(max(0.0, min(old_score, old_score - penalty)))
        except Exception:
            pass
        try:
            old_ds = float(row.get("decision_score"))
            out.at[idx, "decision_score"] = float(max(0.0, min(old_ds, old_ds - penalty)))
        except Exception:
            pass

    try:
        app.state_set("ensemble_last_applied_at", datetime.now(timezone.utc).isoformat())
        app.state_set("ensemble_last_applied_count", int(len(target_idx)))
        app.state_set("ensemble_last_downgrades", int(changed))
        app.state_set("ensemble_last_no_trade", int(no_trade))
        app.state_set("ensemble_runtime_status", "ACTIVE" if enabled else "SHADOW_ONLY")
    except Exception:
        pass
    return out


def install():
    """Patch training and analysis once; fail-open on any challenger problem."""
    if getattr(app, "_ensemble_safety_installed", False):
        return True
    original_train = getattr(app, "train_jst", None)
    original_analyze = getattr(app, "analyze_all", None)
    if not callable(original_train) or not callable(original_analyze):
        return False

    def train_wrapper(universe, *args, **kwargs):
        result = original_train(universe, *args, **kwargs)
        try:
            model = result[0] if isinstance(result, tuple) and result else None
            if model is not None:
                train_challengers(universe)
        except Exception as exc:
            try:
                app.state_set("ensemble_training_error", str(exc)[:800])
            except Exception:
                pass
        return result

    def analyze_wrapper(universe, model=None, *args, **kwargs):
        result = original_analyze(universe, model, *args, **kwargs)
        try:
            return harden_rows(result)
        except Exception as exc:
            try:
                app.state_set("ensemble_apply_error", str(exc)[:800])
            except Exception:
                pass
            return result

    app.train_jst = train_wrapper
    app.analyze_all = analyze_wrapper
    app._ensemble_safety_installed = True
    return True


def status_snapshot():
    bundle = load_bundle()
    if not bundle:
        return {"revision": REVISION, "status": "UNAVAILABLE"}
    meta = dict(bundle.get("meta") or {})
    return {
        "revision": REVISION,
        "status": "ACTIVE" if meta.get("production_enabled") else "SHADOW_ONLY",
        "trained_at": meta.get("trained_at"),
        "best_single": meta.get("best_single"),
        "weights": meta.get("weights"),
        "ensemble_metrics": meta.get("ensemble_metrics"),
        "composite_gain": meta.get("composite_gain"),
        "brier_gain": meta.get("brier_gain"),
    }
