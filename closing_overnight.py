"""Strict Closing / Overnight Intelligence for AI Stock Intelligence V1.8.44."""
from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd

BUY = {"BUY", "STRONG_BUY"}

def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")

def _prob(s: pd.Series) -> pd.Series:
    x = _num(s)
    return x.where(x <= 1.0, x / 100.0)

def select_overnight(res: Optional[pd.DataFrame], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or {}
    c = pd.DataFrame() if res is None else res.copy()
    limit = max(0, min(5, int(cfg.get("overnight_max_picks", 5))))
    min_ai = float(cfg.get("overnight_min_ai_score", 75.0))
    min_p = float(cfg.get("overnight_min_p_up", 0.64))
    min_rr = float(cfg.get("overnight_min_risk_reward", 1.5))
    max_risk = str(cfg.get("overnight_max_risk", "MEDIUM")).upper().strip()

    if c.empty or "decision" not in c.columns or limit == 0:
        return {"status": "NO_OVERNIGHT_TRADE", "picks": pd.DataFrame(), "reason": "No explicit overnight BUY candidate."}

    c = c[c["decision"].astype(str).str.upper().str.strip().isin(BUY)].copy()
    if "ai_score" in c.columns:
        c = c[_num(c["ai_score"]) >= min_ai].copy()
    if "jst_p_up" in c.columns:
        p = _prob(c["jst_p_up"])
        c = c[p.notna() & (p >= min_p)].copy()
    if "risk_reward_2" in c.columns:
        c = c[_num(c["risk_reward_2"]) >= min_rr].copy()
    if "risk" in c.columns and max_risk in {"LOW", "MEDIUM", "HIGH"}:
        ranks = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "EXTREME": 4}
        r = c["risk"].astype(str).str.upper().str.strip().map(ranks).fillna(3)
        c = c[r <= ranks[max_risk]].copy()
    if "market_regime" in c.columns and bool(cfg.get("overnight_block_bearish_ihsg", True)):
        c = c[c["market_regime"].astype(str).str.upper().str.strip() != "BEARISH"].copy()

    if c.empty:
        return {"status": "NO_OVERNIGHT_TRADE", "picks": pd.DataFrame(), "reason": "No candidate passed strict overnight quality gates."}

    cols, asc = [], []
    for col, ascending in [("decision_priority", True), ("decision_score", False), ("relative_rank_score", False), ("ai_score", False), ("jst_p_up", False)]:
        if col in c.columns:
            cols.append(col); asc.append(ascending)
    if cols:
        c = c.sort_values(cols, ascending=asc, kind="stable")
    c = c.head(limit).reset_index(drop=True)
    c["overnight_rank"] = range(1, len(c) + 1)
    return {"status": "OVERNIGHT_READY", "picks": c, "reason": "Strict closing selection for next-session holding; list is never padded."}
