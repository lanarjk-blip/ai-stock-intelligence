"""Post-close evaluation for locked Pre-Market Intelligence picks.

V1.8.43 foundation: evaluate what actually happened without rewriting the
original morning recommendation. The evaluator is deliberately deterministic
so later calibration can learn from auditable outcomes instead of hindsight.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import pandas as pd


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def evaluate_pick(pick: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate one immutable morning pick against the completed session.

    `market` may contain open/high/low/close. Entry uses entry_low/entry_high
    when available, otherwise the morning reference close. Ambiguous sessions
    where target and stop are both touched are explicitly marked AMBIGUOUS
    rather than quietly counted as a win.
    """
    entry_low = _num(pick.get("entry_low"))
    entry_high = _num(pick.get("entry_high"))
    reference = _num(pick.get("close"))
    stop = _num(pick.get("stop_loss"))
    target1 = _num(pick.get("target_1"))
    target2 = _num(pick.get("target_2"))

    day_open = _num(market.get("open"))
    day_high = _num(market.get("high"))
    day_low = _num(market.get("low"))
    day_close = _num(market.get("close"))

    if entry_low is None:
        entry_low = reference
    if entry_high is None:
        entry_high = reference

    entry_touched = bool(
        entry_low is not None and entry_high is not None and
        day_high is not None and day_low is not None and
        day_high >= entry_low and day_low <= entry_high
    )

    stop_hit = bool(entry_touched and stop is not None and day_low is not None and day_low <= stop)
    t1_hit = bool(entry_touched and target1 is not None and day_high is not None and day_high >= target1)
    t2_hit = bool(entry_touched and target2 is not None and day_high is not None and day_high >= target2)

    if not entry_touched:
        outcome = "NOT_TRIGGERED"
        verdict = "SESUAI"
    elif stop_hit and (t1_hit or t2_hit):
        outcome = "AMBIGUOUS"
        verdict = "REVIEW"
    elif t2_hit:
        outcome = "TARGET_2"
        verdict = "SESUAI"
    elif t1_hit:
        outcome = "TARGET_1"
        verdict = "SESUAI"
    elif stop_hit:
        outcome = "STOP_LOSS"
        verdict = "TIDAK_SESUAI"
    else:
        outcome = "OPEN_POSITION"
        verdict = "REVIEW"

    basis = entry_low if entry_low is not None else reference
    return_pct = None
    if basis not in (None, 0) and day_close is not None:
        return_pct = (day_close / basis - 1.0) * 100.0

    return {
        "ticker": str(pick.get("ticker", "")),
        "decision": str(pick.get("decision", "")),
        "entry_touched": entry_touched,
        "stop_hit": stop_hit,
        "target_1_hit": t1_hit,
        "target_2_hit": t2_hit,
        "outcome": outcome,
        "verdict": verdict,
        "return_pct_vs_entry_low": return_pct,
        "session_open": day_open,
        "session_high": day_high,
        "session_low": day_low,
        "session_close": day_close,
    }


def summarize(evaluations: pd.DataFrame) -> Dict[str, Any]:
    """Return compact daily calibration statistics."""
    e = pd.DataFrame() if evaluations is None else evaluations.copy()
    if e.empty:
        return {"count": 0, "sesuai": 0, "tidak_sesuai": 0, "review": 0, "hit_rate": None}

    verdict = e.get("verdict", pd.Series(index=e.index, dtype=str)).astype(str).str.upper()
    sesuai = int((verdict == "SESUAI").sum())
    tidak = int((verdict == "TIDAK_SESUAI").sum())
    review = int((verdict == "REVIEW").sum())
    decided = sesuai + tidak
    return {
        "count": int(len(e)),
        "sesuai": sesuai,
        "tidak_sesuai": tidak,
        "review": review,
        "hit_rate": (sesuai / decided) if decided else None,
    }
