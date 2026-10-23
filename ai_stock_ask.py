"""Grounded AI Stock Ask engine.

Answers are derived only from the application's existing ticker snapshot. The module
never invents prices, probabilities, targets, or external news/fundamental data.
R31 makes TODAY actionability fail closed unless the live Dynamic engine confirms
ENTRY READY with fresh intraday evidence.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import math

# Shared semantic UI patch is installed from an import path already loaded by
# app.py. It performs no Streamlit rendering at import time, so page_config
# remains the first rendering command while dataframe/metric/title behavior is
# normalized for the rest of the session.
try:
    import ui_theme_patch
    ui_theme_patch.install()
    # R84: app.py imports ai_stock_ask directly on every Streamlit startup.
    # Install the direct-provider Time Map live layer here too, so Time Map no
    # longer depends on runtime_health_ui_patch/sitecustomize side effects.
    import timemap_ui_direct_live
    timemap_ui_direct_live.install()
except Exception:
    pass


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
        return {
            "status": "NO_DATA",
            "action": "WAIT",
            "headline": "Ticker belum tersedia.",
            "reason": "AI Stock Ask membutuhkan snapshot ticker dari engine.",
        }

    horizon = str(horizon or "TODAY").upper().strip()
    today = horizon in {"TODAY", "NOW", "INTRADAY"}
    decision = str(s.get("decision") or "WAIT").upper().strip()
    entry = str(s.get("entry_status") or "").upper().strip()
    opportunity_state = str(s.get("state") or s.get("opportunity_state") or "").upper().strip()
    risk = str(s.get("risk") or "UNKNOWN").upper().strip()
    regime = str(s.get("market_regime") or "UNKNOWN").upper().strip()
    candidate_mode = str(s.get("candidate_mode") or "STRICT").upper().strip()
    monitor_only = candidate_mode == "MONITOR_FALLBACK"
    live_required = bool(s.get("live_required"))
    intraday_fresh = bool(s.get("intraday_fresh"))
    price_source = str(s.get("price_source") or "DAILY").upper().strip()
    market_status = str(s.get("market_status") or "").upper().strip()

    ai = _num(s.get("ai_score"))
    pup = _pct(s.get("jst_p_up"))
    if pup is None:
        pup = _pct(s.get("p_up"))
    close = _num(s.get("close"))
    lo = _num(s.get("entry_low")); hi = _num(s.get("entry_high"))
    sl = _num(s.get("stop_loss")); t1 = _num(s.get("target_1")); t2 = _num(s.get("target_2"))

    strong = (
        not monitor_only
        and decision in {"BUY", "STRONG_BUY"}
        and (ai is None or ai >= 72)
        and (pup is None or pup >= 62)
    )
    blocked = decision in {"SELL", "SELL/AVOID", "AVOID", "EXIT", "STRONG_SELL"} or risk in {"HIGH", "EXTREME"}
    if horizon in {"TOMORROW", "NEXT_SESSION", "OVERNIGHT"} and regime == "BEARISH":
        blocked = True

    today_entry_ready = opportunity_state == "ENTRY READY" and entry in {"ACTIVE", "ENTRY READY", "READY", "IN_ZONE"}
    live_blocked = today and (not live_required or not intraday_fresh)

    if blocked:
        action = "AVOID"
    elif today:
        action = "BUY" if strong and today_entry_ready and not live_blocked else "WAIT"
    elif strong:
        action = "BUY"
    else:
        action = "WAIT"

    factors = []
    if ai is not None:
        factors.append(f"AI Score {ai:.1f}")
    if pup is not None:
        factors.append(f"P(UP) {pup:.1f}%")
    factors.append(f"Decision {decision}")
    if opportunity_state:
        factors.append(f"Opportunity {opportunity_state}")
    if monitor_only:
        factors.append("Mode MONITOR ONLY")
    if entry:
        factors.append(f"Entry {entry}")
    if today:
        factors.append("Intraday FRESH" if intraday_fresh and live_required else "Intraday NOT ACTIONABLE")
    factors.append(f"Risk {risk}")
    if regime != "UNKNOWN":
        factors.append(f"IHSG {regime}")
    if market_status:
        factors.append(f"Market {market_status}")

    levels = {
        "close": close,
        "entry_low": lo,
        "entry_high": hi,
        "stop_loss": sl,
        "target_1": t1,
        "target_2": t2,
    }

    if monitor_only:
        guardrail = "Kandidat ini hanya MONITOR karena strict gate Dynamic Opportunity belum lolos; jangan meng-upgrade menjadi BUY."
    elif today and not live_required:
        guardrail = "Market reguler tidak sedang live. TODAY tidak boleh di-upgrade menjadi BUY; gunakan outlook next session bila diperlukan."
    elif today and not intraday_fresh:
        guardrail = "Data intraday sesi berjalan belum segar. BUY TODAY diblokir sampai bukti live kembali fresh."
    elif today and opportunity_state != "ENTRY READY":
        guardrail = f"Dynamic Opportunity belum ENTRY READY ({opportunity_state or 'NO LIVE STATE'}); tetap WAIT."
    else:
        guardrail = "Gunakan level engine yang tersedia; jika kondisi invalidasi terpenuhi, jangan mengejar harga."

    return {
        "status": "READY",
        "ticker": ticker,
        "horizon": horizon,
        "action": action,
        "headline": f"{ticker}: {action} untuk {horizon.replace('_', ' ').title()}",
        "reason": " • ".join(factors),
        "levels": levels,
        "guardrail": guardrail,
        "candidate_mode": candidate_mode,
        "opportunity_state": opportunity_state,
        "live_required": live_required,
        "intraday_fresh": intraday_fresh,
        "price_source": price_source,
        "market_status": market_status,
    }
