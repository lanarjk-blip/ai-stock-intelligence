"""R50 research feedback guard for AI Stock Intelligence.

Turns closed shadow/recommendation outcomes into a conservative feedback layer for
Dynamic Opportunity. The guard is deliberately fail-open until there is enough
sample evidence, so a handful of trades cannot overfit or destabilise production.

It does not retrain JST, rewrite historical predictions, or promote weak signals.
It can only apply a small score reward when evidence is robust, or downgrade/
penalise candidates when realised expectancy/calibration for the same market
regime has been poor.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
import pandas as pd

_INSTALLED = False
_ORIGINAL_SCORE = None
_ORIGINAL_STATE = None
_CACHE = {"at": 0.0, "rows": None}
_LOCK = threading.Lock()

CLOSED_STATUSES = {"TP2", "STOP", "EXPIRED"}


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _norm_probability(value):
    value = _num(value)
    if value is None:
        return None
    if value > 1.0:
        value /= 100.0
    return float(np.clip(value, 0.0, 1.0))


def _dbcon():
    import app
    return app.dbcon()


def _cfg():
    try:
        import app
        return dict(app.CFGD or {})
    except Exception:
        return {}


def _outcomes(force=False):
    """Load immutable actionable recommendations with resolved shadow outcomes."""
    ttl = max(60, int(_cfg().get("research_feedback_cache_seconds", 600)))
    now = time.monotonic()
    with _LOCK:
        if not force and _CACHE.get("rows") is not None and now - float(_CACHE.get("at") or 0) < ttl:
            return _CACHE["rows"].copy()

    try:
        con = _dbcon()
        d = pd.read_sql_query(
            """
            SELECT market_date,ticker,market_regime,p_up,confidence,
                   outcome_status,outcome_return_pct,actionable_at_record
            FROM recommendation_history
            WHERE actionable_at_record=1
              AND outcome_return_pct IS NOT NULL
              AND outcome_status IN ('TP2','STOP','EXPIRED')
            ORDER BY id
            """,
            con,
        )
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        d = pd.DataFrame()

    if not d.empty:
        d["outcome_return_pct"] = pd.to_numeric(d["outcome_return_pct"], errors="coerce")
        d["p_up_norm"] = d["p_up"].map(_norm_probability)
        d["confidence_norm"] = d["confidence"].map(_norm_probability)
        d["market_regime"] = d["market_regime"].fillna("UNKNOWN").astype(str).str.upper()
        d = d.dropna(subset=["outcome_return_pct"])

    with _LOCK:
        _CACHE.update({"at": now, "rows": d.copy()})
    return d


def _max_drawdown(returns_pct):
    arr = np.asarray(list(returns_pct), dtype=float)
    if arr.size == 0:
        return None
    equity = np.cumprod(1.0 + arr / 100.0)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity / peak - 1.0) * 100.0
    return float(drawdown.min()) if drawdown.size else None


def summarize_outcomes(frame, *, roundtrip_cost_pct=0.0):
    """Return calibration and risk-adjusted realised metrics for a resolved sample."""
    if frame is None or frame.empty:
        return {
            "samples": 0,
            "win_rate": None,
            "predicted_up": None,
            "calibration_gap": None,
            "gross_expectancy": None,
            "net_expectancy": None,
            "profit_factor": None,
            "max_drawdown": None,
        }

    d = frame.copy()
    d["ret"] = pd.to_numeric(d["outcome_return_pct"], errors="coerce")
    d = d.dropna(subset=["ret"])
    if d.empty:
        return summarize_outcomes(pd.DataFrame())

    cost = max(0.0, float(roundtrip_cost_pct or 0.0))
    d["net_ret"] = d["ret"] - cost
    wins = d[d["net_ret"] > 0]
    losses = d[d["net_ret"] < 0]
    win_rate = float(len(wins) / len(d))

    pup = pd.to_numeric(d.get("p_up_norm", pd.Series(dtype=float)), errors="coerce").dropna()
    predicted = float(pup.mean()) if len(pup) else None
    cal_gap = abs(predicted - win_rate) if predicted is not None else None

    gross_win = float(wins["net_ret"].sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses["net_ret"].sum())) if len(losses) else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else None)

    return {
        "samples": int(len(d)),
        "win_rate": win_rate,
        "predicted_up": predicted,
        "calibration_gap": cal_gap,
        "gross_expectancy": float(d["ret"].mean()),
        "net_expectancy": float(d["net_ret"].mean()),
        "profit_factor": pf,
        "max_drawdown": _max_drawdown(d["net_ret"].values),
    }


def regime_feedback(regime, *, force=False):
    """Use same-regime evidence when sufficient, otherwise a larger global sample."""
    cfg = _cfg()
    min_regime = max(10, int(cfg.get("research_feedback_min_regime_samples", 20)))
    min_global = max(min_regime, int(cfg.get("research_feedback_min_global_samples", 30)))
    cost = max(0.0, float(cfg.get("research_roundtrip_cost_pct", 0.35)))

    d = _outcomes(force=force)
    if d.empty:
        result = summarize_outcomes(d, roundtrip_cost_pct=cost)
        result.update({"scope": "NONE", "regime": str(regime or "UNKNOWN").upper(), "ready": False})
        return result

    wanted = str(regime or "UNKNOWN").upper()
    same = d[d["market_regime"] == wanted]
    if len(same) >= min_regime:
        sample = same
        scope = "REGIME"
    elif len(d) >= min_global:
        sample = d
        scope = "GLOBAL"
    else:
        sample = d.iloc[0:0]
        scope = "INSUFFICIENT"

    result = summarize_outcomes(sample, roundtrip_cost_pct=cost)
    result.update({"scope": scope, "regime": wanted, "ready": scope in {"REGIME", "GLOBAL"}})
    return result


def feedback_adjustment(row):
    """Return a bounded score adjustment from realised historical quality.

    Weak realised expectancy, poor profit factor, large drawdown, and material
    probability miscalibration are penalised. Positive evidence earns only a
    small reward because the guard exists to reduce false confidence, not create it.
    """
    regime = getattr(row, "market_regime", None)
    if hasattr(row, "get"):
        regime = row.get("market_regime", regime)
    m = regime_feedback(regime)
    if not m.get("ready"):
        return 0.0, m

    exp = _num(m.get("net_expectancy"), 0.0)
    pf = _num(m.get("profit_factor"))
    gap = _num(m.get("calibration_gap"))
    dd = _num(m.get("max_drawdown"), 0.0)

    adj = 0.0
    if exp < -0.50:
        adj -= 8.0
    elif exp < 0.0:
        adj -= 4.0

    if pf is not None:
        if pf < 0.80:
            adj -= 6.0
        elif pf < 1.00:
            adj -= 3.0

    if gap is not None:
        if gap > 0.20:
            adj -= 5.0
        elif gap > 0.12:
            adj -= 2.5

    if dd <= -15.0:
        adj -= 4.0
    elif dd <= -10.0:
        adj -= 2.0

    if exp > 0.50 and pf is not None and pf >= 1.30 and (gap is None or gap <= 0.10) and dd > -10.0:
        adj += 3.0

    return float(np.clip(adj, -15.0, 3.0)), m


def quality_block(metrics):
    """True only for sufficiently sampled, materially weak realised evidence."""
    if not metrics or not metrics.get("ready"):
        return False
    exp = _num(metrics.get("net_expectancy"), 0.0)
    pf = _num(metrics.get("profit_factor"))
    gap = _num(metrics.get("calibration_gap"))
    dd = _num(metrics.get("max_drawdown"), 0.0)
    return bool(
        exp <= -0.50
        or (pf is not None and pf < 0.80)
        or (gap is not None and gap > 0.22)
        or dd <= -18.0
    )


def _patched_score(row, ts):
    base = float(_ORIGINAL_SCORE(row, ts))
    adj, _ = feedback_adjustment(row)
    return float(np.clip(base + adj, 0.0, 100.0))


def _patched_state(row, ts, score):
    base = str(_ORIGINAL_STATE(row, ts, score) or "MONITOR").upper()
    _, metrics = feedback_adjustment(row)
    if base == "ENTRY READY" and quality_block(metrics):
        return "DEVELOPING"
    return base


def install():
    """Patch Dynamic Opportunity after the existing live-precision patch."""
    global _INSTALLED, _ORIGINAL_SCORE, _ORIGINAL_STATE
    if _INSTALLED:
        return True
    try:
        import dynamic_engine
        if getattr(dynamic_engine._score, "_r50_research_feedback", False):
            _INSTALLED = True
            return True
        _ORIGINAL_SCORE = dynamic_engine._score
        _ORIGINAL_STATE = dynamic_engine._state
        _patched_score._r50_research_feedback = True
        _patched_state._r50_research_feedback = True
        dynamic_engine._score = _patched_score
        dynamic_engine._state = _patched_state
        _INSTALLED = True
        return True
    except Exception:
        return False
