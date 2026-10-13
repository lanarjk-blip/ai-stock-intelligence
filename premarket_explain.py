"""Auditable SEBAB -> AKIBAT explanation for V1.8.43 Pre-Market picks."""
from __future__ import annotations

from typing import Any, Dict
import pandas as pd


def _num(value: Any):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _pct(value: Any):
    x = _num(value)
    if x is None:
        return None
    return x * 100.0 if x <= 1.0 else x


def explain_pick(row: Dict[str, Any]) -> str:
    """Build a compact explanation only from factors actually present in a row."""
    reasons = []
    ai = _num(row.get("ai_score"))
    pup = _pct(row.get("jst_p_up"))
    risk = str(row.get("risk", "")).upper().strip()
    regime = str(row.get("market_regime", "")).upper().strip()
    rr = _num(row.get("risk_reward_2"))
    rel = _pct(row.get("relative_percentile"))

    if ai is not None:
        reasons.append(f"AI Score {ai:.1f}")
    if pup is not None:
        reasons.append(f"P(UP) {pup:.1f}%")
    if rel is not None:
        reasons.append(f"relative strength {rel:.1f}%")
    if rr is not None:
        reasons.append(f"R/R {rr:.2f}")
    if risk:
        reasons.append(f"risk {risk}")
    if regime:
        reasons.append(f"IHSG {regime}")

    sebab = ", ".join(reasons) if reasons else "lolos strict high-conviction gate"
    decision = str(row.get("decision", "BUY")).upper().strip()
    akibat = "kandidat BUY Pre-Market" if decision in {"BUY", "STRONG_BUY"} else "kandidat tidak actionable"
    return f"SEBAB: {sebab} -> AKIBAT: {akibat}; wajib lolos Opening Confirmation sebelum entry."
