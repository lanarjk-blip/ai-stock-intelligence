"""Strict Time Map accuracy guard for every ticker.

Principles:
- Historical slot statistics are evidence, never live confirmation.
- A live/actionable status requires fresh intraday data from the current market date.
- Historical bullish/bearish calls are downgraded when current price/volume momentum disagrees.
- The guard is ticker-agnostic and must be applied to every Time Map result.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

WIB = ZoneInfo("Asia/Jakarta")


def _num(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _today_wib():
    return datetime.now(WIB).date()


def live_confirmation(intraday: pd.DataFrame, current_price=None):
    """Return conservative current-session direction and data quality."""
    if intraday is None or intraday.empty or "ts" not in intraday.columns:
        return {"fresh": False, "direction": "UNKNOWN", "strength": 0.0, "reason": "NO_INTRADAY"}
    d = intraday.copy()
    d["ts"] = pd.to_datetime(d["ts"], errors="coerce", utc=True)
    d = d.dropna(subset=["ts"]).sort_values("ts")
    if d.empty:
        return {"fresh": False, "direction": "UNKNOWN", "strength": 0.0, "reason": "INVALID_INTRADAY"}
    local = d["ts"].dt.tz_convert(WIB)
    d = d[local.dt.date == _today_wib()].copy()
    if d.empty:
        return {"fresh": False, "direction": "UNKNOWN", "strength": 0.0, "reason": "STALE_MARKET_DATE"}
    if "close" not in d.columns:
        return {"fresh": False, "direction": "UNKNOWN", "strength": 0.0, "reason": "NO_CLOSE"}
    close = pd.to_numeric(d["close"], errors="coerce").dropna()
    if len(close) < 2:
        return {"fresh": False, "direction": "UNKNOWN", "strength": 0.0, "reason": "INSUFFICIENT_BARS"}
    last = _num(current_price, _num(close.iloc[-1]))
    first = _num(close.iloc[0])
    prev = _num(close.iloc[-2])
    session_ret = last / first - 1 if first else 0.0
    bar_ret = last / prev - 1 if prev else 0.0
    recent = close.tail(min(4, len(close)))
    recent_ret = last / _num(recent.iloc[0], last) - 1 if len(recent) > 1 else bar_ret
    volume_confirm = 0.0
    if "volume" in d.columns:
        vol = pd.to_numeric(d["volume"], errors="coerce").dropna()
        if len(vol) >= 4:
            base = _num(vol.iloc[:-1].tail(6).median(), 0.0)
            if base > 0:
                volume_confirm = float(np.clip(_num(vol.iloc[-1], 0.0) / base - 1.0, -1.0, 2.0))
    composite = 0.45 * session_ret + 0.35 * recent_ret + 0.20 * bar_ret
    # Require a meaningful move. Tiny noise must not certify a direction.
    if composite >= 0.0020 and session_ret > -0.0015:
        direction = "UP"
    elif composite <= -0.0020 and session_ret < 0.0015:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"
    strength = float(np.clip(abs(composite) / 0.012 + max(0.0, volume_confirm) * 0.10, 0.0, 1.0))
    return {"fresh": True, "direction": direction, "strength": strength,
            "session_return": session_ret, "recent_return": recent_ret,
            "bar_return": bar_ret, "volume_confirm": volume_confirm, "reason": "CURRENT_SESSION"}


def reconcile_time_map(time_map: pd.DataFrame, intraday: pd.DataFrame, current_price=None):
    """Reconcile every Time Map row against fresh live evidence.

    This function never upgrades historical evidence into ACTIVE solely from live
    price. It can confirm it, or downgrade it when evidence is stale/contradictory.
    """
    if time_map is None or time_map.empty:
        return time_map
    out = time_map.copy()
    live = live_confirmation(intraday, current_price=current_price)
    out["historical_status"] = out.get("status", "NEUTRAL")
    out["live_direction"] = live.get("direction", "UNKNOWN")
    out["live_strength"] = live.get("strength", 0.0)
    out["live_fresh"] = bool(live.get("fresh"))
    out["alignment"] = "UNCONFIRMED"
    out["trusted"] = False

    markers = {"BREAK", "PRE-CLOSING", "CLOSING"}
    for idx, row in out.iterrows():
        session = str(row.get("session", "")).upper()
        hist = str(row.get("historical_status", row.get("status", "NEUTRAL"))).upper()
        if session in markers or hist == "CHECKPOINT":
            out.at[idx, "alignment"] = "CHECKPOINT"
            continue
        samples = _num(row.get("samples"), 0.0)
        conf = _num(row.get("confidence_pct"), 0.0)
        up = _num(row.get("up_pct"), 0.0)
        down = _num(row.get("down_pct"), 0.0)
        edge = up - down
        # Trust gate: enough observations + confidence + separation.
        hist_trusted = samples >= 20 and conf >= 55.0 and abs(edge) >= 10.0
        if not live.get("fresh"):
            out.at[idx, "status"] = "WAIT" if hist in {"ACTIVE", "DEVELOPING"} else hist
            out.at[idx, "alignment"] = "NO_LIVE_CONFIRMATION"
            out.at[idx, "note"] = "Historical evidence only; current-session intraday is not fresh."
            continue
        direction = live.get("direction")
        bullish_hist = hist in {"ACTIVE", "DEVELOPING"} or edge >= 10.0
        bearish_hist = hist == "WAIT" or edge <= -10.0
        if bullish_hist and direction == "DOWN":
            out.at[idx, "status"] = "WAIT"
            out.at[idx, "alignment"] = "DIVERGENCE"
            out.at[idx, "note"] = "Historical bullish slot rejected: current-session momentum is down."
        elif bearish_hist and direction == "UP":
            # Never turn bearish history into ACTIVE automatically.
            out.at[idx, "status"] = "NEUTRAL"
            out.at[idx, "alignment"] = "DIVERGENCE"
            out.at[idx, "note"] = "Historical bearish slot conflicts with current-session momentum."
        elif direction == "NEUTRAL":
            out.at[idx, "status"] = "NEUTRAL" if hist in {"ACTIVE", "DEVELOPING"} else hist
            out.at[idx, "alignment"] = "MIXED"
            out.at[idx, "note"] = "Live movement is not strong enough to confirm historical direction."
        else:
            aligned = (bullish_hist and direction == "UP") or (bearish_hist and direction == "DOWN")
            out.at[idx, "alignment"] = "MATCH" if aligned else "MIXED"
            if hist in {"ACTIVE", "DEVELOPING"} and not hist_trusted:
                out.at[idx, "status"] = "NEUTRAL"
                out.at[idx, "note"] = "Historical sample/confidence gate not strong enough for trusted ACTIVE."
            elif aligned and hist_trusted:
                out.at[idx, "trusted"] = True
                out.at[idx, "note"] = "Historical slot and fresh current-session evidence agree."
    return out
