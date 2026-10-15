"""Post-close evaluation for locked Pre-Market Intelligence picks.

Evaluate what actually happened without rewriting the original morning decision.
Actionable BUY decisions and monitor-only candidates are deliberately separated so
calibration cannot count a WAIT/MONITOR candidate as a trading win.
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

    BUY/STRONG_BUY rows are evaluated as trades. WAIT/MONITOR rows remain useful
    audit observations but receive REVIEW/MONITOR_ONLY, so they do not inflate
    trading hit-rate merely because price later crossed a target.
    """
    decision = str(pick.get("decision") or "").upper().strip()
    actionable = decision in {"BUY", "STRONG_BUY"}
    entry_low = _num(pick.get("entry_low"))
    entry_high = _num(pick.get("entry_high"))
    reference = _num(pick.get("reference_price"))
    if reference is None:
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

    if not actionable:
        outcome = "MONITOR_ONLY"
        verdict = "REVIEW"
    elif not entry_touched:
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
        "decision": decision,
        "actionable": actionable,
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
    """Return compact daily calibration statistics.

    REVIEW rows, including MONITOR_ONLY candidates, are excluded from hit-rate.
    """
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
