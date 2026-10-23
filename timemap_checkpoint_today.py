"""Per-checkpoint current-market-day Time Map overlay.

This layer replaces the one-row TODAY summary with a full realized checkpoint map.
Historical clock-slot probabilities remain visible only as baseline evidence below
it. No previous-market-day bar may satisfy a current-day checkpoint.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import math
import re

import pandas as pd

_INSTALLED = False
_ORIGINAL_AUGMENT = None


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _slot_minutes(value):
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _history_direction(row):
    up = _num(row.get("UP %"), 0.0)
    down = _num(row.get("DOWN %"), 0.0)
    sideways = _num(row.get("Sideways %"), 0.0)
    if sideways >= max(up, down):
        return "SIDEWAYS"
    return "UP" if up >= down else "DOWN"


def _history_probability(row, direction):
    if direction == "UP":
        return _num(row.get("UP %"), 0.0)
    if direction == "DOWN":
        return _num(row.get("DOWN %"), 0.0)
    return _num(row.get("Sideways %"), 0.0)


def _prepare_day(base, ticker):
    status, live_required, market_date, wib = base._session()
    result = {
        "ticker": ticker,
        "market_date": market_date,
        "market_status": status,
        "live_required": bool(live_required),
        "wib": wib,
        "fresh": False,
        "age_min": None,
        "reason": "",
        "source": "DIRECT PROVIDER",
        "frame": pd.DataFrame(),
    }
    try:
        data = base._provider_frame(ticker)
        if data is None or data.empty or "ts" not in data.columns or "close" not in data.columns:
            result["reason"] = "NO INTRADAY BARS"
            return result
        data = data.copy()
        data["ts"] = pd.to_datetime(data["ts"], utc=True, errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in data.columns:
                data[col] = None
            data[col] = pd.to_numeric(data[col], errors="coerce")
        data = data.dropna(subset=["ts", "close"]).sort_values("ts")
        data["wib"] = data["ts"].dt.tz_convert("Asia/Jakarta")
        # Absolute lifecycle boundary: yesterday is never today's evidence.
        data = data[data["wib"].dt.date.astype(str) == str(market_date)].copy()
        if data.empty:
            result["reason"] = "NO BAR FOR ACTIVE MARKET DAY"
            return result
        data["minute"] = data["wib"].dt.hour * 60 + data["wib"].dt.minute
        now = pd.Timestamp(wib)
        now = now.tz_localize("Asia/Jakarta") if now.tzinfo is None else now.tz_convert("Asia/Jakarta")
        age = max(0.0, (now.tz_convert("UTC") - data.iloc[-1]["ts"]).total_seconds() / 60.0)
        try:
            cfg = base._cfg()
            stale_limit = max(10, int(cfg.get("intraday_live_stale_minutes") or 25))
        except Exception:
            stale_limit = 25
        pause = status in {"BREAK", "AFTER_MARKET"}
        fresh = bool(age <= stale_limit or pause)
        result.update(
            {
                "frame": data,
                "fresh": fresh,
                "age_min": age,
                "reason": "SESSION PAUSE" if pause else ("FRESH" if fresh else "DATA LIVE BELUM SEGAR"),
            }
        )
    except Exception as exc:
        result["reason"] = type(exc).__name__.upper()
    return result


def _checkpoint_metrics(day, minute):
    """Use only bars that existed by this checkpoint, never future tape."""
    if day is None or day.empty:
        return None
    upto = day[day["minute"] <= int(minute)].copy()
    if upto.empty:
        return None
    last = upto.iloc[-1]
    last_minute = int(last["minute"])
    # A missing provider bar may not be silently filled using an old checkpoint.
    if int(minute) - last_minute > 35:
        return None

    first = day.iloc[0]
    current = _num(last.get("close"))
    open_price = _num(first.get("open"), _num(first.get("close")))
    if current is None or open_price in (None, 0):
        return None

    return_pct = ((current / open_price) - 1.0) * 100.0
    previous = _num(upto.iloc[-2].get("close")) if len(upto) >= 2 else current
    momentum_pct = ((current / previous) - 1.0) * 100.0 if previous else 0.0

    high = _num(upto["high"].max(), _num(upto["close"].max()))
    low = _num(upto["low"].min(), _num(upto["close"].min()))
    range_position = (
        ((current - low) / (high - low)) * 100.0
        if high is not None and low is not None and high > low
        else 50.0
    )

    volumes = pd.to_numeric(upto["volume"], errors="coerce").dropna()
    baseline_volume = _num(volumes.iloc[:-1].tail(6).median(), 0.0) if len(volumes) >= 3 else 0.0
    volume_ratio = float(volumes.iloc[-1]) / baseline_volume if len(volumes) >= 3 and baseline_volume > 0 else None

    volume = pd.to_numeric(upto["volume"], errors="coerce").fillna(0.0)
    closes = pd.to_numeric(upto["close"], errors="coerce")
    total_volume = float(volume.sum())
    vwap = float((closes * volume).sum() / total_volume) if total_volume > 0 else None
    versus_vwap = ((current / vwap) - 1.0) * 100.0 if current and vwap else None

    score = 0
    if return_pct >= 0.35:
        score += 2
    elif return_pct <= -0.35:
        score -= 2
    elif return_pct >= 0.10:
        score += 1
    elif return_pct <= -0.10:
        score -= 1
    if momentum_pct >= 0.15:
        score += 1
    elif momentum_pct <= -0.15:
        score -= 1
    if range_position >= 65:
        score += 1
    elif range_position <= 35:
        score -= 1
    if versus_vwap is not None:
        if versus_vwap >= 0.15:
            score += 1
        elif versus_vwap <= -0.15:
            score -= 1
    if volume_ratio is not None and volume_ratio >= 1.20:
        if momentum_pct > 0:
            score += 1
        elif momentum_pct < 0:
            score -= 1

    direction = "UP" if score >= 2 else ("DOWN" if score <= -2 else "SIDEWAYS")
    confidence = min(95.0, 50.0 + abs(score) * 8.0 + (5.0 if volume_ratio is not None and volume_ratio >= 1.20 else 0.0))
    return {
        "bar_minute": last_minute,
        "bar_time": last["wib"].strftime("%H:%M"),
        "bars": int(len(upto)),
        "price": current,
        "open": open_price,
        "high": high,
        "low": low,
        "return_pct": return_pct,
        "momentum_pct": momentum_pct,
        "volume_ratio": volume_ratio,
        "range_position_pct": range_position,
        "price_vs_vwap_pct": versus_vwap,
        "score": int(score),
        "confidence_pct": confidence,
        "direction": direction,
    }


def _blank_today_row(history_row, ticker, day_state):
    row = dict(history_row)
    row.update(
        {
            "Data Type": "TODAY CHECKPOINT",
            "Live Ticker": ticker,
            "Market Date": day_state.get("market_date"),
            "Market Status": day_state.get("market_status"),
            "Live Source": day_state.get("source"),
            "Checkpoint State": "WAIT",
            "Checkpoint Bar": None,
            "Live Price": None,
            "Live Open": None,
            "Live High": None,
            "Live Low": None,
            "Live Return %": None,
            "Live Momentum %": None,
            "Volume Ratio": None,
            "Range Pos %": None,
            "Vs VWAP %": None,
            "Live Score": 0,
            "Live Confidence %": 0.0,
            "Live Direction": "WAIT",
            "Live Confirmation": "WAIT",
            "Live Match": "WAIT",
            "Match %": None,
            "Live Age m": None,
            "Live Bars": 0,
            "Live Reason": "",
            "Baseline UP %": _num(history_row.get("UP %")),
            "Baseline Sideways %": _num(history_row.get("Sideways %")),
            "Baseline DOWN %": _num(history_row.get("DOWN %")),
            # Today's rows must never visually masquerade as historical probabilities.
            "UP %": None,
            "Sideways %": None,
            "DOWN %": None,
            "Mean Return %": None,
            "Confidence %": None,
            "Samples": None,
        }
    )
    return row


def _checkpoint_rows(frame, st, base):
    ticker = base._active_ticker(st)
    if not ticker:
        return None
    time_col = base._time_column(frame)
    if time_col is None:
        return None

    state = _prepare_day(base, ticker)
    day = state["frame"]
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    current_minute = now.hour * 60 + now.minute
    latest_bar_minute = int(day.iloc[-1]["minute"]) if not day.empty else None
    market_date = str(state.get("market_date") or "")
    try:
        market_weekday = pd.Timestamp(market_date).dayofweek
    except Exception:
        market_weekday = now.weekday()

    rows = []
    for _, hist in frame.iterrows():
        row = _blank_today_row(hist, ticker, state)
        minute = _slot_minutes(hist.get(time_col))
        session = str(hist.get("Sesi") or "").upper()

        if minute is None:
            row["Checkpoint State"] = "SCHEDULE"
            row["Live Reason"] = "TIME SLOT TIDAK TERBACA"
            rows.append(row)
            continue

        if session in {"BREAK", "PRE-CLOSING", "CLOSING"}:
            row["Checkpoint State"] = session
            row["Live Confirmation"] = "WAIT • MARKET SCHEDULE"
            row["Live Match"] = session
            row["Live Reason"] = "MARKET SCHEDULE"
            rows.append(row)
            continue

        # IDX Friday has no normal 13:30 regular-session checkpoint.
        if market_weekday == 4 and minute == 13 * 60 + 30:
            row["Checkpoint State"] = "NOT TRADING FRIDAY"
            row["Live Confirmation"] = "WAIT • NOT TRADING"
            row["Live Match"] = "NOT TRADING"
            row["Live Reason"] = "FRIDAY SESSION II STARTS 14:00"
            rows.append(row)
            continue

        if day.empty:
            row["Checkpoint State"] = "NO LIVE DATA"
            row["Live Confirmation"] = "WAIT • DATA NOT FRESH"
            row["Live Match"] = "DATA LIVE BELUM SEGAR"
            row["Live Reason"] = state.get("reason") or "NO BAR FOR ACTIVE MARKET DAY"
            rows.append(row)
            continue

        if minute > current_minute or (latest_bar_minute is not None and minute > latest_bar_minute + 35):
            row["Checkpoint State"] = "WAIT FUTURE"
            row["Live Confirmation"] = "WAIT • CHECKPOINT BELUM TERJADI"
            row["Live Match"] = "WAIT FUTURE"
            row["Live Reason"] = "CHECKPOINT BELUM TERJADI"
            rows.append(row)
            continue

        metrics = _checkpoint_metrics(day, minute)
        if metrics is None:
            row["Checkpoint State"] = "NO BAR"
            row["Live Confirmation"] = "WAIT • NO BAR"
            row["Live Match"] = "NO LIVE CONFIRMATION"
            row["Live Reason"] = "NO BAR FOR CHECKPOINT"
            rows.append(row)
            continue

        row.update(
            {
                "Checkpoint State": "REALIZED TODAY",
                "Checkpoint Bar": metrics["bar_time"],
                "Live Price": metrics["price"],
                "Live Open": metrics["open"],
                "Live High": metrics["high"],
                "Live Low": metrics["low"],
                "Live Return %": metrics["return_pct"],
                "Live Momentum %": metrics["momentum_pct"],
                "Volume Ratio": metrics["volume_ratio"],
                "Range Pos %": metrics["range_position_pct"],
                "Vs VWAP %": metrics["price_vs_vwap_pct"],
                "Live Score": metrics["score"],
                "Live Confidence %": metrics["confidence_pct"],
                "Live Direction": metrics["direction"],
                "Live Bars": metrics["bars"],
                "Live Reason": "TODAY CHECKPOINT",
            }
        )
        if latest_bar_minute is not None and metrics["bar_minute"] == latest_bar_minute:
            row["Live Age m"] = state.get("age_min")

        hist_direction = _history_direction(hist)
        live_direction = metrics["direction"]
        match_pct = _history_probability(hist, live_direction)
        if live_direction == hist_direction:
            confirmation = "CONFIRMED" if match_pct >= 55 else "CONFIRMED • WEAK"
        elif live_direction == "SIDEWAYS" or hist_direction == "SIDEWAYS":
            confirmation = "MIXED • WAIT"
        else:
            confirmation = "DIVERGENCE • WAIT"

        is_latest = latest_bar_minute is not None and metrics["bar_minute"] == latest_bar_minute
        if is_latest and not state.get("fresh"):
            confirmation = "WAIT • DATA NOT FRESH"
        elif is_latest and state.get("market_status") == "BREAK":
            confirmation = f"{confirmation} • BREAK"

        row["Live Confirmation"] = confirmation
        row["Live Match"] = confirmation
        row["Match %"] = round(float(match_pct), 1)
        rows.append(row)

    return pd.DataFrame(rows)


def _augment(frame, st):
    import timemap_ui_direct_live as base

    if not base._is_map(frame):
        return frame

    today = _checkpoint_rows(frame, st, base)
    if today is None:
        # Preserve existing fail-closed behavior when ticker/time context is absent.
        return _ORIGINAL_AUGMENT(frame, st) if _ORIGINAL_AUGMENT else frame

    history = frame.copy()
    history["Data Type"] = "HISTORICAL BASELINE"
    for col in today.columns:
        if col not in history.columns:
            history[col] = None
    for col in history.columns:
        if col not in today.columns:
            today[col] = None

    out = pd.concat([today[history.columns], history], ignore_index=True)
    preferred = [
        "Waktu", "Sesi", "Horizon", "Data Type", "Live Ticker", "Market Date", "Market Status",
        "Checkpoint State", "Checkpoint Bar", "Live Price", "Live Return %", "Live Momentum %",
        "Volume Ratio", "Range Pos %", "Vs VWAP %", "Live Score", "Live Confidence %", "Live Direction",
        "Live Confirmation", "Live Match", "Match %", "Live Age m", "Live Bars", "Live Reason", "Live Source",
        "Baseline UP %", "Baseline Sideways %", "Baseline DOWN %", "Samples", "UP %", "Sideways %", "DOWN %",
        "Mean Return %", "Confidence %", "Status", "Catatan",
    ]
    columns = [col for col in preferred if col in out.columns]
    columns += [col for col in out.columns if col not in columns]
    return out.loc[:, columns]


def install():
    global _INSTALLED, _ORIGINAL_AUGMENT
    if _INSTALLED:
        return True
    import timemap_ui_direct_live as base

    if getattr(base._augment, "_asi_r88_checkpoint_today", False):
        _INSTALLED = True
        return True
    _ORIGINAL_AUGMENT = base._augment
    _augment._asi_r88_checkpoint_today = True
    base._augment = _augment
    _INSTALLED = True
    return True
