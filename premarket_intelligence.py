"""Strict Pre-Market Intelligence selector for AI Stock Intelligence V1.8.43.

This module deliberately separates pre-market trade candidates from the wider
Dynamic Opportunity pool. It never pads a list merely to reach Top-5: when no
candidate clears every configured quality gate, the correct output is NO_TRADE.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import pandas as pd

BUY_DECISIONS = {"BUY", "STRONG_BUY"}


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _prob(series: pd.Series) -> pd.Series:
    p = _num(series)
    return p.where(p <= 1.0, p / 100.0)


def select_high_conviction(res: Optional[pd.DataFrame], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return strict pre-market picks and an explicit status.

    Required gates are applied only when their source column exists, except
    decision: a pre-market BUY list must explicitly be BUY/STRONG_BUY. This
    prevents WAIT/WATCH rows from leaking into the locked morning selection.
    """
    cfg = config or {}
    c = pd.DataFrame() if res is None else res.copy()
    max_picks = max(0, min(5, int(cfg.get("premarket_max_picks", 5))))
    min_ai = float(cfg.get("premarket_min_ai_score", 60.0))
    min_p_up = float(cfg.get("premarket_min_p_up", 0.55))
    max_risk = str(cfg.get("premarket_max_risk", "HIGH")).upper().strip()

    if c.empty or "decision" not in c.columns or max_picks == 0:
        return {"status": "NO_TRADE", "picks": pd.DataFrame(), "reason": "No explicit high-conviction BUY candidate."}

    decision = c["decision"].astype(str).str.upper().str.strip()
    c = c[decision.isin(BUY_DECISIONS)].copy()

    if "ai_score" in c.columns:
        c = c[_num(c["ai_score"]) >= min_ai].copy()

    if "jst_p_up" in c.columns:
        p = _prob(c["jst_p_up"])
        c = c[p.notna() & (p >= min_p_up)].copy()

    # Optional conservative risk ceiling. LOW < MEDIUM < HIGH < EXTREME.
    if "risk" in c.columns and max_risk in {"LOW", "MEDIUM", "HIGH"}:
        risk_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "EXTREME": 4}
        rr = c["risk"].astype(str).str.upper().str.strip().map(risk_rank).fillna(3)
        c = c[rr <= risk_rank[max_risk]].copy()

    if c.empty:
        return {
            "status": "NO_TRADE",
            "picks": pd.DataFrame(),
            "reason": f"No BUY/STRONG_BUY passed AI>={min_ai:.1f} and P(UP)>={min_p_up:.0%} gates.",
        }

    cols, asc = [], []
    for col, ascending in [
        ("decision_priority", True),
        ("decision_score", False),
        ("relative_rank_score", False),
        ("ai_score", False),
        ("jst_p_up", False),
    ]:
        if col in c.columns:
            cols.append(col)
            asc.append(ascending)
    if cols:
        c = c.sort_values(cols, ascending=asc, kind="stable")

    c = c.head(max_picks).reset_index(drop=True)
    c["premarket_rank"] = range(1, len(c) + 1)
    return {
        "status": "LOCKED" if not c.empty else "NO_TRADE",
        "picks": c,
        "reason": "Strict high-conviction pre-market selection; list is never padded.",
    }
