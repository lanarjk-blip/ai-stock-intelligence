"""Grounded AI Stock Ask engine for AI Stock Intelligence V1.8.45.

Answers are derived only from the application's existing ticker snapshot. The module
never invents prices, probabilities, targets, or external news/fundamental data.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import math


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _pct(v: Any) -> Optional[float]:
    x = _num(v)
    if x is None:
        return None
    return x * 100.0 if abs(x) <= 1.0 else x


def answer_stock(snapshot: Optional[Dict[str, Any]], horizon: str = "TODAY") -> Dict[str, Any]:
    s = dict(snapshot or {})
    ticker = str(s.get("ticker") or "").upper().replace(".JK", "").strip()
    if not ticker:
        return {"status": "NO_DATA", "action": "WAIT", "headline": "Ticker belum tersedia.", "reason": "AI Stock Ask membutuhkan snapshot ticker dari engine."}

    decision = str(s.get("decision") or "WAIT").upper().strip()
    entry = str(s.get("entry_status") or "").upper().strip()
    risk = str(s.get("risk") or "UNKNOWN").upper().strip()
    regime = str(s.get("market_regime") or "UNKNOWN").upper().strip()
    ai = _num(s.get("ai_score"))
    pup = _pct(s.get("jst_p_up"))
    close = _num(s.get("close"))
    lo = _num(s.get("entry_low")); hi = _num(s.get("entry_high"))
    sl = _num(s.get("stop_loss")); t1 = _num(s.get("target_1")); t2 = _num(s.get("target_2"))
    horizon = str(horizon or "TODAY").upper().strip()

    strong = decision in {"BUY", "STRONG_BUY"} and (ai is None or ai >= 72) and (pup is None or pup >= 62)
    blocked = decision in {"SELL", "AVOID", "EXIT"} or risk in {"HIGH", "EXTREME"}
    if horizon in {"TOMORROW", "NEXT_SESSION", "OVERNIGHT"} and regime == "BEARISH":
        blocked = True

    if blocked:
        action = "AVOID"
    elif strong and (not entry or entry in {"ACTIVE", "ENTRY READY", "READY", "IN_ZONE"}):
        action = "BUY"
    elif strong:
        action = "WAIT"
    else:
        action = "WAIT"

    factors = []
    if ai is not None: factors.append(f"AI Score {ai:.1f}")
    if pup is not None: factors.append(f"P(UP) {pup:.1f}%")
    factors.append(f"Decision {decision}")
    if entry: factors.append(f"Entry {entry}")
    factors.append(f"Risk {risk}")
    if regime != "UNKNOWN": factors.append(f"IHSG {regime}")

    levels = {"close": close, "entry_low": lo, "entry_high": hi, "stop_loss": sl, "target_1": t1, "target_2": t2}
    return {
        "status": "READY",
        "ticker": ticker,
        "horizon": horizon,
        "action": action,
        "headline": f"{ticker}: {action} untuk {horizon.replace('_', ' ').title()}",
        "reason": " • ".join(factors),
        "levels": levels,
        "guardrail": "Gunakan level engine yang tersedia; jika entry belum aktif, jangan mengejar harga.",
    }
