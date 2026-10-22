"""R51 capital-preservation circuit breaker.

Uses only resolved shadow outcomes from the system's own recommendations. It is a
model-risk guard, not a broker/account-position tracker. The guard never creates
BUY states; it can only reduce score or downgrade aggressiveness after a recent
loss streak or drawdown. Small samples are fail-open.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

_INSTALLED = False
_ORIGINAL_SCORE = None
_ORIGINAL_STATE = None
_CACHE = {"at": 0.0, "metrics": None, "db_key": None}


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _cfg():
    try:
        import app
        return dict(app.CFGD or {})
    except Exception:
        return {}


def _db_identity():
    try:
        import app
        return str(getattr(app, "DB", ""))
    except Exception:
        return ""


def _dbcon():
    import app
    return app.dbcon()


def _recent_returns(force=False):
    cfg = _cfg()
    ttl = max(60, int(cfg.get("capital_guard_cache_seconds", 300)))
    now = time.monotonic()
    db_key = _db_identity()
    if (
        not force
        and _CACHE.get("metrics") is not None
        and _CACHE.get("db_key") == db_key
        and now - float(_CACHE.get("at") or 0) < ttl
    ):
        return dict(_CACHE["metrics"])

    window = max(6, int(cfg.get("capital_guard_window", 20)))
    try:
        con = _dbcon()
        d = pd.read_sql_query(
            """
            SELECT id,return_pct,status
            FROM shadow_positions
            WHERE status IN ('TP2','STOP','EXPIRED')
              AND return_pct IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            con,
            params=(window,),
        )
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        d = pd.DataFrame()

    if d.empty:
        metrics = {"samples": 0, "loss_streak": 0, "recent_avg": None, "max_drawdown": None, "ready": False}
        _CACHE.update({"at": now, "metrics": metrics, "db_key": db_key})
        return dict(metrics)

    d["return_pct"] = pd.to_numeric(d["return_pct"], errors="coerce")
    d = d.dropna(subset=["return_pct"])
    recent = d["return_pct"].tolist()  # newest -> oldest
    streak = 0
    for ret in recent:
        if ret < 0:
            streak += 1
        else:
            break

    chrono = np.asarray(list(reversed(recent)), dtype=float)
    equity = np.cumprod(1.0 + chrono / 100.0)
    peak = np.maximum.accumulate(equity)
    dd = (equity / peak - 1.0) * 100.0
    max_dd = float(dd.min()) if len(dd) else None

    min_samples = max(5, int(cfg.get("capital_guard_min_samples", 8)))
    metrics = {
        "samples": int(len(recent)),
        "loss_streak": int(streak),
        "recent_avg": float(np.mean(recent)) if recent else None,
        "max_drawdown": max_dd,
        "ready": len(recent) >= min_samples,
    }
    _CACHE.update({"at": now, "metrics": metrics, "db_key": db_key})
    return dict(metrics)


def capital_adjustment(metrics=None):
    metrics = metrics or _recent_returns()
    if not metrics.get("ready"):
        return 0.0
    streak = int(metrics.get("loss_streak") or 0)
    dd = _num(metrics.get("max_drawdown"), 0.0)
    avg = _num(metrics.get("recent_avg"), 0.0)
    adj = 0.0
    if streak >= 5:
        adj -= 12.0
    elif streak >= 4:
        adj -= 9.0
    elif streak >= 3:
        adj -= 6.0
    if dd <= -12.0:
        adj -= 8.0
    elif dd <= -8.0:
        adj -= 5.0
    elif dd <= -5.0:
        adj -= 2.0
    if avg < -0.5:
        adj -= 3.0
    return float(np.clip(adj, -18.0, 0.0))


def capital_state(base_state, metrics=None):
    state = str(base_state or "MONITOR").upper()
    metrics = metrics or _recent_returns()
    if not metrics.get("ready"):
        return state
    streak = int(metrics.get("loss_streak") or 0)
    dd = _num(metrics.get("max_drawdown"), 0.0)
    if state == "ENTRY READY":
        if streak >= 5 or dd <= -12.0:
            return "MONITOR"
        if streak >= 3 or dd <= -8.0:
            return "DEVELOPING"
    return state


def _patched_score(row, ts):
    base = float(_ORIGINAL_SCORE(row, ts))
    return float(np.clip(base + capital_adjustment(), 0.0, 100.0))


def _patched_state(row, ts, score):
    base = _ORIGINAL_STATE(row, ts, score)
    return capital_state(base)


def install():
    global _INSTALLED, _ORIGINAL_SCORE, _ORIGINAL_STATE
    if _INSTALLED:
        return True
    try:
        import dynamic_engine
        if getattr(dynamic_engine._score, "_r51_capital_guard", False):
            _INSTALLED = True
            return True
        _ORIGINAL_SCORE = dynamic_engine._score
        _ORIGINAL_STATE = dynamic_engine._state
        _patched_score._r51_capital_guard = True
        _patched_state._r51_capital_guard = True
        dynamic_engine._score = _patched_score
        dynamic_engine._state = _patched_state
        _INSTALLED = True
        return True
    except Exception:
        return False
