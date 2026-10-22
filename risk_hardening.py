"""R52-R54 risk hardening for AI Stock Intelligence.

R52: correlation/crowding guard for the selected opportunity set.
R53: adaptive risk-size multiplier based on confidence, stop distance and risk.
R54: walk-forward degradation detector based on validation-fold deterioration.

The module never promotes a state or increases an opportunity score. Missing or
insufficient evidence is fail-open. The wrapper runs after Dynamic Opportunity
has built its normal result, then conservatively reduces aggressiveness only when
risk evidence is material.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

_INSTALLED = False
_ORIGINAL_UPDATE = None


def _cfg():
    try:
        import app
        return dict(app.CFGD or {})
    except Exception:
        return {}


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _returns_matrix(tickers: Iterable[str], lookback: int = 70) -> pd.DataFrame:
    symbols = [str(x).upper() for x in tickers if str(x).strip()]
    if len(symbols) < 2:
        return pd.DataFrame()
    try:
        import app
        con = app.dbcon()
        marks = ",".join(["?"] * len(symbols))
        rows = pd.read_sql_query(
            f"SELECT ticker,date,close FROM prices WHERE ticker IN ({marks}) ORDER BY date",
            con,
            params=tuple(symbols),
        )
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return pd.DataFrame()
    if rows.empty:
        return pd.DataFrame()
    rows["close"] = pd.to_numeric(rows["close"], errors="coerce")
    rows = rows.dropna(subset=["close"])
    pivot = rows.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    if len(pivot) > lookback:
        pivot = pivot.tail(lookback)
    return pivot.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)


def crowding_adjustments(frame: pd.DataFrame, returns: Optional[pd.DataFrame] = None) -> Dict[str, dict]:
    """Calculate bounded penalties for highly correlated simultaneous ideas."""
    if frame is None or frame.empty or "ticker" not in frame.columns:
        return {}
    cfg = _cfg()
    min_obs = max(20, int(cfg.get("crowding_min_observations", 30)))
    warn = float(cfg.get("crowding_corr_warn", 0.82))
    severe = max(warn, float(cfg.get("crowding_corr_severe", 0.92)))
    x = frame.copy()
    sort_col = "opportunity_score" if "opportunity_score" in x.columns else None
    if sort_col:
        x = x.sort_values(sort_col, ascending=False)
    ret = returns if returns is not None else _returns_matrix(x["ticker"].tolist())
    if ret is None or ret.empty:
        return {}

    accepted = []
    result = {}
    for _, row in x.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        if ticker not in ret.columns:
            accepted.append(ticker)
            result[ticker] = {"penalty": 0.0, "max_corr": None, "high_corr_peers": 0, "ready": False}
            continue
        max_corr = None
        high_peers = 0
        for peer in accepted:
            if peer not in ret.columns:
                continue
            pair = ret[[ticker, peer]].dropna()
            if len(pair) < min_obs:
                continue
            corr = float(pair[ticker].corr(pair[peer]))
            if not math.isfinite(corr):
                continue
            max_corr = corr if max_corr is None else max(max_corr, corr)
            if corr >= severe:
                high_peers += 1
        penalty = 0.0
        if max_corr is not None:
            if max_corr >= severe:
                penalty = -8.0 if high_peers < 2 else -12.0
            elif max_corr >= warn:
                penalty = -4.0
        result[ticker] = {
            "penalty": penalty,
            "max_corr": max_corr,
            "high_corr_peers": high_peers,
            "ready": max_corr is not None,
        }
        accepted.append(ticker)
    return result


def adaptive_size_multiplier(row, capital_metrics=None, degradation=None) -> float:
    """Return a conservative risk-budget multiplier in [0.20, 1.00]."""
    confidence = _num(row.get("confidence") if hasattr(row, "get") else None, 0.5)
    if confidence is not None and confidence > 1.0:
        confidence /= 100.0
    confidence = float(np.clip(confidence if confidence is not None else 0.5, 0.0, 1.0))
    close = _num(row.get("close") if hasattr(row, "get") else None)
    stop = _num(row.get("stop_loss") if hasattr(row, "get") else None)
    risk = str(row.get("risk", "MEDIUM") if hasattr(row, "get") else "MEDIUM").upper()

    mult = 0.45 + 0.55 * confidence
    mult *= {"LOW": 1.0, "MEDIUM": 0.80, "HIGH": 0.55}.get(risk, 0.70)
    if close and stop and close > 0 and stop < close:
        stop_pct = (close - stop) / close
        if stop_pct >= 0.08:
            mult *= 0.55
        elif stop_pct >= 0.05:
            mult *= 0.70
        elif stop_pct >= 0.03:
            mult *= 0.85

    cm = capital_metrics or {}
    if cm.get("ready"):
        streak = int(cm.get("loss_streak") or 0)
        dd = _num(cm.get("max_drawdown"), 0.0)
        if streak >= 5 or dd <= -12:
            mult *= 0.40
        elif streak >= 3 or dd <= -8:
            mult *= 0.65

    dg = degradation or {}
    if dg.get("ready") and dg.get("degraded"):
        mult *= 0.65 if not dg.get("severe") else 0.45
    return float(np.clip(mult, 0.20, 1.00))


def _validation_fold_scores(payload) -> list[float]:
    folds = payload.get("folds", []) if isinstance(payload, dict) else []
    values = []
    keys = ("balanced_accuracy", "accuracy", "f1", "f1_score", "precision_up", "score")
    for fold in folds:
        if not isinstance(fold, dict):
            continue
        value = None
        for key in keys:
            value = _num(fold.get(key))
            if value is not None:
                break
        if value is None:
            continue
        if value > 1.0:
            value /= 100.0
        values.append(float(value))
    return values


def validation_degradation(payload=None) -> dict:
    """Detect relative walk-forward deterioration without assuming a magic accuracy target."""
    if payload is None:
        try:
            import app
            path = os.path.join(app.DB_DIR, "model_validation.json")
            if not os.path.exists(path):
                return {"ready": False, "degraded": False, "severe": False, "folds": 0}
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return {"ready": False, "degraded": False, "severe": False, "folds": 0}
    scores = _validation_fold_scores(payload or {})
    if len(scores) < 4:
        return {"ready": False, "degraded": False, "severe": False, "folds": len(scores)}
    half = len(scores) // 2
    previous = float(np.mean(scores[:half]))
    recent = float(np.mean(scores[half:]))
    delta = recent - previous
    cfg = _cfg()
    warn_drop = abs(float(cfg.get("validation_degradation_warn", 0.05)))
    severe_drop = max(warn_drop, abs(float(cfg.get("validation_degradation_severe", 0.10))))
    return {
        "ready": True,
        "degraded": delta <= -warn_drop,
        "severe": delta <= -severe_drop,
        "folds": len(scores),
        "previous_mean": previous,
        "recent_mean": recent,
        "delta": delta,
    }


def harden_frame(frame: pd.DataFrame, returns=None, capital_metrics=None, degradation=None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    out = frame.copy()
    crowd = crowding_adjustments(out, returns=returns)
    dg = degradation if degradation is not None else validation_degradation()
    try:
        if capital_metrics is None:
            import capital_preservation
            capital_metrics = capital_preservation._recent_returns()
    except Exception:
        capital_metrics = {}

    sizes = []
    corr_values = []
    for idx, row in out.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        info = crowd.get(ticker, {})
        penalty = float(info.get("penalty") or 0.0)
        if dg.get("ready") and dg.get("degraded"):
            penalty += -5.0 if not dg.get("severe") else -9.0
        if "opportunity_score" in out.columns:
            out.at[idx, "opportunity_score"] = float(np.clip(_num(row.get("opportunity_score"), 0.0) + penalty, 0.0, 100.0))
        state = str(row.get("state", "MONITOR") or "MONITOR").upper()
        if state == "ENTRY READY":
            if int(info.get("high_corr_peers") or 0) >= 2:
                state = "MONITOR"
            elif penalty <= -4.0:
                state = "DEVELOPING"
        out.at[idx, "state"] = state
        sizes.append(adaptive_size_multiplier(row, capital_metrics=capital_metrics, degradation=dg))
        corr_values.append(info.get("max_corr"))
    out["risk_size_multiplier"] = sizes
    out["crowding_max_corr"] = corr_values
    out["validation_degraded"] = bool(dg.get("ready") and dg.get("degraded"))
    return out


def _persist_hardening(out: pd.DataFrame):
    if out is None or out.empty:
        return
    try:
        import app
        con = app.dbcon()
        for _, row in out.iterrows():
            ticker = str(row.get("ticker", ""))
            score = _num(row.get("opportunity_score"))
            state = str(row.get("state", "MONITOR"))
            if score is not None:
                con.execute(
                    "UPDATE dynamic_opportunities SET opportunity_score=?, state=? WHERE ticker=?",
                    (score, state, ticker),
                )
        con.commit()
        con.close()
        snapshot = [
            {
                "ticker": str(r.get("ticker", "")),
                "risk_size_multiplier": round(float(r.get("risk_size_multiplier", 1.0)), 4),
                "crowding_max_corr": None if pd.isna(r.get("crowding_max_corr")) else round(float(r.get("crowding_max_corr")), 4),
                "validation_degraded": bool(r.get("validation_degraded", False)),
            }
            for _, r in out.iterrows()
        ]
        app.state_set("risk_hardening_snapshot", json.dumps(snapshot, separators=(",", ":")))
    except Exception:
        try:
            con.close()
        except Exception:
            pass


def _patched_update(*args, **kwargs):
    base = _ORIGINAL_UPDATE(*args, **kwargs)
    hardened = harden_frame(base)
    _persist_hardening(hardened)
    return hardened


def install():
    global _INSTALLED, _ORIGINAL_UPDATE
    if _INSTALLED:
        return True
    try:
        import dynamic_engine
        if getattr(dynamic_engine.update, "_r52_r54_risk_hardening", False):
            _INSTALLED = True
            return True
        _ORIGINAL_UPDATE = dynamic_engine.update
        _patched_update._r52_r54_risk_hardening = True
        dynamic_engine.update = _patched_update
        _INSTALLED = True
        return True
    except Exception:
        return False
