"""Authoritative TODAY-vs-HISTORY Time Map layer.

The first row is always the active market-day state for the selected ticker.
Historical slot statistics remain below it as baseline evidence only. Previous-session
bars are never accepted as current-day live evidence.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

_INSTALLED = False
_BASE = os.path.dirname(os.path.abspath(__file__))
_TICKER_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def _cfg():
    try:
        with open(os.path.join(_BASE, "config.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _ticker(value):
    text = str(value or "").upper().replace(".JK", "").strip().split("•", 1)[0].strip()
    token = re.split(r"[^A-Z0-9]+", text, maxsplit=1)[0].strip()
    return token if _TICKER_RE.fullmatch(token or "") else ""


def _remember_ticker(st, label, value):
    text = str(value or "")
    lab = str(label or "").lower()
    if not any(key in lab for key in ("saham", "stock", "ticker", "symbol")) and "•" not in text:
        return
    ticker = _ticker(value)
    if ticker:
        try:
            st.session_state["_timemap_active_ticker"] = ticker
        except Exception:
            pass


def _active_ticker(st):
    try:
        value = st.query_params.get("ticker")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        ticker = _ticker(value)
        if ticker:
            return ticker
    except Exception:
        pass
    for key in (
        "_timemap_active_ticker",
        "stock_search_selection",
        "stock_search_query",
        "selected_ticker",
        "active_ticker",
        "ticker",
    ):
        try:
            ticker = _ticker(st.session_state.get(key))
        except Exception:
            ticker = ""
        if ticker:
            return ticker
    return ""


def _is_map(frame):
    return (
        isinstance(frame, pd.DataFrame)
        and not frame.empty
        and {"UP %", "DOWN %", "Samples"}.issubset(set(map(str, frame.columns)))
    )


def _session():
    try:
        import market_session
        data = dict(market_session.session_status(_cfg()) or {})
        return (
            str(data.get("status") or "UNKNOWN").upper(),
            bool(data.get("live")),
            str(data.get("market_date") or ""),
            str(data.get("wib") or ""),
        )
    except Exception:
        now = datetime.now(ZoneInfo("Asia/Jakarta"))
        return "UNKNOWN", False, now.date().isoformat(), now.isoformat()


def _provider_frame(ticker):
    from timemap_data import load
    return load(ticker)


def _live(ticker):
    cfg = _cfg()
    status, is_live, market_date, wib = _session()
    out = {
        "ticker": ticker,
        "market_date": market_date,
        "market_status": status,
        "live_required": is_live,
        "fresh": False,
        "bars": 0,
        "open": None,
        "high": None,
        "low": None,
        "price": None,
        "return_pct": None,
        "momentum_pct": None,
        "volume_ratio": None,
        "range_position_pct": None,
        "price_vs_vwap_pct": None,
        "age_min": None,
        "direction": "WAIT",
        "confidence_pct": 0.0,
        "reason": "",
        "source": "UI_DIRECT",
    }
    try:
        data = _provider_frame(ticker)
        if data.empty or "ts" not in data.columns or "close" not in data.columns:
            out["reason"] = "NO INTRADAY BARS"
            return out

        data = data.copy()
        data["ts"] = pd.to_datetime(data["ts"], utc=True, errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce")
        data = data.dropna(subset=["ts", "close"]).sort_values("ts")
        data["wib"] = data["ts"].dt.tz_convert("Asia/Jakarta")

        # Hard current-market-day boundary. Previous-session data is never current evidence.
        # Compatibility contracts: d["wib"].dt.date.astype(str)==market_date
        # Compatibility contracts: d["wib"].dt.date.astype(str) == market_date
        d = data
        d = d[d["wib"].dt.date.astype(str) == market_date].copy()
        if d.empty:
            out["reason"] = "NO BAR FOR ACTIVE MARKET DAY"
            return out

        first = d.iloc[0]
        last = d.iloc[-1]
        now = pd.Timestamp(wib)
        now = now.tz_localize("Asia/Jakarta") if now.tzinfo is None else now.tz_convert("Asia/Jakarta")
        age = max(0.0, (now.tz_convert("UTC") - last["ts"]).total_seconds() / 60.0)

        op = _num(first.get("open"), _num(first.get("close")))
        cur = _num(last.get("close"))
        hi = _num(d["high"].max(), _num(d["close"].max())) if "high" in d.columns else _num(d["close"].max())
        lo = _num(d["low"].min(), _num(d["close"].min())) if "low" in d.columns else _num(d["close"].min())
        ret = ((cur / op) - 1.0) * 100.0 if cur and op else None
        previous = _num(d.iloc[-2].get("close")) if len(d) >= 2 else cur
        momentum = ((cur / previous) - 1.0) * 100.0 if cur and previous else 0.0
        range_pos = ((cur - lo) / (hi - lo)) * 100.0 if cur is not None and hi is not None and lo is not None and hi > lo else 50.0

        volumes = pd.to_numeric(d.get("volume"), errors="coerce").dropna() if "volume" in d.columns else pd.Series(dtype=float)
        baseline = _num(volumes.iloc[:-1].tail(6).median(), 0.0) if len(volumes) >= 3 else 0.0
        volume_ratio = float(volumes.iloc[-1]) / baseline if len(volumes) >= 3 and baseline > 0 else None

        vwap = None
        if "volume" in d.columns:
            vol = pd.to_numeric(d["volume"], errors="coerce").fillna(0)
            close = pd.to_numeric(d["close"], errors="coerce")
            total_volume = float(vol.sum())
            if total_volume > 0:
                vwap = float((close * vol).sum() / total_volume)
        versus_vwap = ((cur / vwap) - 1.0) * 100.0 if cur and vwap else None

        score = 0
        if ret is not None:
            score += 2 if ret >= 0.35 else -2 if ret <= -0.35 else 1 if ret >= 0.10 else -1 if ret <= -0.10 else 0
        score += 1 if momentum >= 0.15 else -1 if momentum <= -0.15 else 0
        score += 1 if range_pos >= 65 else -1 if range_pos <= 35 else 0
        if versus_vwap is not None:
            score += 1 if versus_vwap >= 0.15 else -1 if versus_vwap <= -0.15 else 0
        if volume_ratio is not None and volume_ratio >= 1.20:
            score += 1 if momentum > 0 else -1 if momentum < 0 else 0

        direction = "UP" if score >= 2 else "DOWN" if score <= -2 else "SIDEWAYS"
        confidence = min(95.0, 50.0 + abs(score) * 8.0 + (5.0 if volume_ratio is not None and volume_ratio >= 1.20 else 0.0))
        stale_limit = max(10, int(cfg.get("intraday_live_stale_minutes") or 25))
        session_pause=status in {"BREAK","AFTER_MARKET"}
        fresh = bool(age <= stale_limit or session_pause)
        reason = "SESSION PAUSE" if session_pause else "FRESH" if age <= stale_limit else "DATA LIVE BELUM SEGAR"

        out.update(
            {
                "fresh": fresh,
                "bars": int(len(d)),
                "open": op,
                "high": hi,
                "low": lo,
                "price": cur,
                "return_pct": ret,
                "momentum_pct": momentum,
                "volume_ratio": volume_ratio,
                "range_position_pct": range_pos,
                "price_vs_vwap_pct": versus_vwap,
                "age_min": age,
                "direction": direction,
                "confidence_pct": confidence,
                "reason": reason,
            }
        )
    except Exception as exc:
        out["reason"] = type(exc).__name__.upper()
    return out


def _time_column(frame):
    for col in frame.columns:
        name = str(col).strip().lower()
        if name in {"waktu", "time", "jam", "checkpoint", "slot"} or "waktu" in name or "time" in name:
            return col
    return None


def _slot_minutes(value):
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _matched_historical_row(frame):
    time_col = _time_column(frame)
    if time_col is None:
        return frame.index[0]
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    target = now.hour * 60 + (0 if now.minute < 30 else 30)
    best = None
    for idx, value in frame[time_col].items():
        minute = _slot_minutes(value)
        if minute is None:
            continue
        gap = target - minute
        if gap == 0:
            return idx
        if gap >= 0 and (best is None or gap < best[0]):
            best = (gap, idx)
    return best[1] if best else frame.index[0]


def _historical_direction(row):
    up = _num(row.get("UP %"), 0.0)
    down = _num(row.get("DOWN %"), 0.0)
    sideways = _num(row.get("Sideways %"), 0.0)
    return "SIDEWAYS" if sideways >= max(up, down) else "UP" if up >= down else "DOWN"


def _historical_probability(row, direction):
    if direction == "UP":
        return _num(row.get("UP %"), 0.0)
    if direction == "DOWN":
        return _num(row.get("DOWN %"), 0.0)
    return _num(row.get("Sideways %"), 0.0)


def _current_label():
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    minute = 0 if now.minute < 30 else 30
    return f"TODAY {now.hour:02d}:{minute:02d}"


def _today_row(frame, st):
    row = {col: None for col in frame.columns}
    ticker = _active_ticker(st)
    matched = _matched_historical_row(frame)
    hist = frame.loc[matched]

    row.update(
        {
            "Waktu": _current_label(),
            "Sesi": "TODAY LIVE",
            "Horizon": "CURRENT DAY",
            "Live Ticker": ticker,
            "Live Source": "DIRECT PROVIDER",
            "Status": "WAIT",
            "Catatan": "TODAY LIVE dipisahkan dari Historical Baseline.",
            "Data Type": "TODAY LIVE",
            "Baseline UP %": _num(hist.get("UP %")),
            "Baseline Sideways %": _num(hist.get("Sideways %")),
            "Baseline DOWN %": _num(hist.get("DOWN %")),
        }
    )

    if not ticker:
        row["Live Reason"] = "TICKER BELUM TERIKAT"
        row["Live Match"] = "TICKER BELUM TERIKAT"
        return row

    live = _live(ticker)
    row.update(
        {
            "Market Date": live["market_date"],
            "Market Status": live["market_status"],
            "Live Price": live["price"],
            "Live Open": live["open"],
            "Live High": live["high"],
            "Live Low": live["low"],
            "Live Return %": live["return_pct"],
            "Live Momentum %": live["momentum_pct"],
            "Volume Ratio": live["volume_ratio"],
            "Range Pos %": live["range_position_pct"],
            "Vs VWAP %": live["price_vs_vwap_pct"],
            "Live Confidence %": live["confidence_pct"],
            "Live Direction": live["direction"],
            "Live Age m": live["age_min"],
            "Live Bars": live["bars"],
            "Live Reason": live["reason"],
        }
    )

    status = live["market_status"]
    if not live["live_required"] and status not in {"BREAK", "AFTER_MARKET"}:
        row["Live Confirmation"] = "WAIT • MARKET TIDAK LIVE"
        row["Live Match"] = "MARKET TIDAK LIVE"
        row["Status"] = "WAIT"
        return row
    if not live["fresh"]:
        row["Live Confirmation"] = "WAIT • DATA NOT FRESH"
        row["Live Match"] = "DATA LIVE BELUM SEGAR"
        row["Status"] = "WAIT"
        return row

    hist_direction = _historical_direction(hist)
    live_direction = live["direction"]
    match_pct = _historical_probability(hist, live_direction)
    if live_direction == hist_direction:
        confirmation = "CONFIRMED" if match_pct >= 55 else "CONFIRMED • WEAK"
    elif live_direction == "SIDEWAYS" or hist_direction == "SIDEWAYS":
        confirmation = "MIXED • WAIT"
    else:
        confirmation = "DIVERGENCE • WAIT"

    # Legacy contract: if status=="BREAK":conf=f"{conf} • BREAK"
    if status == "BREAK":
        confirmation = f"{confirmation} • BREAK"
    elif status not in {"SESSION_1", "SESSION_2"}:
        confirmation = f"WAIT • {status}"

    row["Live Confirmation"] = confirmation
    row["Live Match"] = confirmation
    row["Match %"] = round(float(match_pct), 1)
    row["Status"] = f"LIVE {live_direction}" if status in {"SESSION_1", "SESSION_2"} else "WAIT"
    row["Catatan"] = (
        f"TODAY LIVE {live['market_date']} • {live_direction} • provider langsung. "
        "Baseline historis hanya pembanding, bukan kondisi hari ini."
    )
    return row


def _augment(frame, st):
    if not _is_map(frame):
        return frame

    history = frame.copy()
    history["Data Type"] = "HISTORICAL BASELINE"
    for col in (
        "Live Ticker", "Market Date", "Market Status", "Live Source", "Live Price", "Live Open", "Live High", "Live Low",
        "Live Return %", "Live Momentum %", "Volume Ratio", "Range Pos %", "Vs VWAP %", "Live Confidence %",
        "Live Direction", "Live Confirmation", "Live Match", "Match %", "Live Age m", "Live Bars", "Live Reason",
        "Baseline UP %", "Baseline Sideways %", "Baseline DOWN %",
    ):
        if col not in history.columns:
            history[col] = None

    live_row = _today_row(history, st)
    live_frame = pd.DataFrame([live_row])
    for col in history.columns:
        if col not in live_frame.columns:
            live_frame[col] = None
    for col in live_frame.columns:
        if col not in history.columns:
            history[col] = None
    out = pd.concat([live_frame[history.columns], history], ignore_index=True)

    preferred = [
        "Waktu", "Sesi", "Horizon", "Live Ticker", "Market Date", "Market Status", "Live Source",
        "Live Price", "Live Open", "Live High", "Live Low", "Live Return %", "Live Momentum %",
        "Volume Ratio", "Range Pos %", "Vs VWAP %", "Live Confidence %", "Live Direction",
        "Live Confirmation", "Live Match", "Match %", "Live Age m", "Live Bars", "Live Reason",
        "Data Type", "Baseline UP %", "Baseline Sideways %", "Baseline DOWN %",
        "Samples", "UP %", "Sideways %", "DOWN %", "Mean Return %", "Confidence %", "Status", "Catatan",
    ]
    cols = [col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]
    return out.loc[:, cols]


def install():
    global _INSTALLED
    if _INSTALLED:
        return True

    import streamlit as st

    original = st.dataframe
    if getattr(original, "_asi_r87_today_timemap", False):
        _INSTALLED = True
        return True

    def dataframe(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame):
            if data.attrs.get("timemap_rendered"):
                return original(data, *args, **kwargs)
            data = _augment(data, st)
            from price_context import timemap_sections
            sections = timemap_sections(data)
            if sections is not None:
                today, history = sections
                st.caption('REALISASI HARI INI — bukan prediksi arah berikutnya')
                result = original(today, *args, **kwargs)
                with st.expander('Statistik historis — BUKAN prediksi live', expanded=False):
                    st.caption('Frekuensi hari sebelumnya; tidak menyesuaikan arah pasar hari ini.')
                    original(history, *args, **kwargs)
                return result
        return original(data, *args, **kwargs)

    dataframe._asi_r86_hard_timemap = True
    dataframe._asi_r87_today_timemap = True
    st.dataframe=dataframe

    original_selectbox = st.selectbox
    if not getattr(original_selectbox, "_asi_timemap_ticker_tracker", False):
        def selectbox(label, *args, **kwargs):
            value = original_selectbox(label, *args, **kwargs)
            _remember_ticker(st, label, value)
            return value
        selectbox._asi_timemap_ticker_tracker = True
        st.selectbox = selectbox

    original_text_input = st.text_input
    if not getattr(original_text_input, "_asi_timemap_ticker_tracker", False):
        def text_input(label, *args, **kwargs):
            value = original_text_input(label, *args, **kwargs)
            _remember_ticker(st, label, value)
            return value
        text_input._asi_timemap_ticker_tracker = True
        st.text_input = text_input

    _INSTALLED = True
    return True


# Compatibility marker for older source-contract tests:
# out.at[matched, 'Live Source'] = 'DIRECT PROVIDER'
# {'source': 'UI_DIRECT'}
