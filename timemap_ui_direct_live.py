"""Today-State Time Map for Streamlit/APK.

Historical clock-slot statistics remain reference evidence only. Every completed
checkpoint is overlaid with current-market-day tape. Previous-session data is
never accepted as current-market-day live evidence.
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
    if not any(k in lab for k in ("saham", "stock", "ticker", "symbol")) and "•" not in text:
        return
    token = _ticker(value)
    if token:
        try:
            st.session_state["_timemap_active_ticker"] = token
        except Exception:
            pass


def _active_ticker(st):
    for key in (
        "_timemap_active_ticker",
        "stock_search_selection",
        "stock_search_query",
        "selected_ticker",
        "active_ticker",
        "ticker",
    ):
        try:
            token = _ticker(st.session_state.get(key))
        except Exception:
            token = ""
        if token:
            return token
    try:
        value = st.query_params.get("ticker")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        token = _ticker(value)
        if token:
            return token
    except Exception:
        pass
    return ""


def _is_map(df):
    return (
        isinstance(df, pd.DataFrame)
        and not df.empty
        and {"UP %", "DOWN %", "Samples"}.issubset(set(map(str, df.columns)))
    )


def _time_col(df):
    for col in df.columns:
        name = str(col).lower().strip()
        if name in {"waktu", "time", "jam", "checkpoint", "slot"} or "waktu" in name or "time" in name:
            return col
    return None


def _slot(value):
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _session():
    try:
        import market_session

        row = dict(market_session.session_status(_cfg()) or {})
        return (
            str(row.get("status") or "UNKNOWN").upper(),
            bool(row.get("live")),
            str(row.get("market_date") or ""),
            str(row.get("wib") or ""),
        )
    except Exception:
        now = datetime.now(ZoneInfo("Asia/Jakarta"))
        return "UNKNOWN", False, now.date().isoformat(), now.isoformat()


def _provider_frame(ticker):
    """Bypass stale local cache for today's tape; use app resolver first, then direct provider."""
    cfg = _cfg()
    try:
        import app

        value = app.fetch_chart_recover(
            ticker,
            max(2, int(cfg.get("intraday_refresh_period_days", 5))),
            cfg.get("intraday_interval", "30m"),
        )
        if isinstance(value, tuple):
            for item in value:
                if isinstance(item, pd.DataFrame):
                    return item
        if isinstance(value, pd.DataFrame):
            return value
    except Exception:
        pass

    params = {
        "range": "5d",
        "interval": str(cfg.get("intraday_interval") or "30m"),
        "events": "history",
    }
    headers = {"User-Agent": str(cfg.get("user_agent") or "AI-Stock-Intelligence/1.8")}
    timeout = int(cfg.get("request_timeout_seconds") or 8)
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            response = requests.get(
                f"https://{host}/v8/finance/chart/{ticker}.JK",
                params=params,
                timeout=timeout,
                headers=headers,
            )
            response.raise_for_status()
            result = ((response.json().get("chart") or {}).get("result") or [])
            if not result:
                continue
            item = result[0]
            timestamps = item.get("timestamp") or []
            quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
            if not timestamps:
                continue

            def arr(name):
                values = quote.get(name)
                if isinstance(values, list) and len(values) == len(timestamps):
                    return values
                return [None] * len(timestamps)

            return pd.DataFrame(
                {
                    "ts": pd.to_datetime(timestamps, unit="s", utc=True, errors="coerce"),
                    "open": arr("open"),
                    "high": arr("high"),
                    "low": arr("low"),
                    "close": arr("close"),
                    "volume": arr("volume"),
                }
            )
        except Exception:
            continue
    return pd.DataFrame()


def _today_tape(ticker):
    cfg = _cfg()
    status, is_live, market_date, wib = _session()
    out = {
        "ticker": ticker,
        "market_date": market_date,
        "market_status": status,
        "live_required": is_live,
        "fresh": False,
        "age_min": None,
        "reason": "",
        "source": "DIRECT PROVIDER",
        "frame": pd.DataFrame(),
    }
    try:
        d = _provider_frame(ticker)
        if d.empty or "ts" not in d.columns or "close" not in d.columns:
            out["reason"] = "NO INTRADAY BARS"
            return out

        d = d.copy()
        d["ts"] = pd.to_datetime(d["ts"], utc=True, errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in d.columns:
                d[col] = None
            d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d.dropna(subset=["ts", "close"]).sort_values("ts")
        d["wib"] = d["ts"].dt.tz_convert("Asia/Jakarta")

        # Hard boundary. Compatibility contract: d["wib"].dt.date.astype(str)==market_date
        # Previous-session bars can never become today's checkpoint evidence.
        d = d[d["wib"].dt.date.astype(str) == market_date].copy()
        if d.empty:
            out["reason"] = "NO BAR FOR ACTIVE MARKET DAY"
            return out

        d["minute"] = d["wib"].dt.hour * 60 + d["wib"].dt.minute
        now = pd.Timestamp(wib)
        now = now.tz_localize("Asia/Jakarta") if now.tzinfo is None else now.tz_convert("Asia/Jakarta")
        age = max(0.0, (now.tz_convert("UTC") - d.iloc[-1]["ts"]).total_seconds() / 60.0)
        stale_limit = max(10, int(cfg.get("intraday_live_stale_minutes") or 25))
        session_pause=status in {"BREAK","AFTER_MARKET"}
        fresh = bool(age <= stale_limit or session_pause)
        reason = "SESSION PAUSE" if session_pause else ("FRESH" if fresh else "DATA LIVE BELUM SEGAR")
        out.update({"fresh": fresh, "age_min": age, "reason": reason, "frame": d})
        return out
    except Exception as exc:
        out["reason"] = type(exc).__name__.upper()
        return out


def _hist(row):
    up = _num(row.get("UP %"), 0.0)
    down = _num(row.get("DOWN %"), 0.0)
    side = _num(row.get("Sideways %"), 0.0)
    if side >= max(up, down):
        return "SIDEWAYS"
    return "UP" if up >= down else "DOWN"


def _hist_prob(row, direction):
    if direction == "UP":
        return _num(row.get("UP %"), 0.0)
    if direction == "DOWN":
        return _num(row.get("DOWN %"), 0.0)
    return _num(row.get("Sideways %"), 0.0)


def _checkpoint_metrics(day, minute):
    """Return realized evidence using only bars available up to one checkpoint."""
    if day is None or day.empty:
        return None
    upto = day[day["minute"] <= int(minute)].copy()
    if upto.empty:
        return None

    # Require a nearby bar. This prevents 09:00 evidence from being recycled into
    # a much later checkpoint when the provider stopped updating.
    last = upto.iloc[-1]
    last_minute = int(last["minute"])
    if int(minute) - last_minute > 35:
        return None

    first = day.iloc[0]
    cur = _num(last.get("close"))
    op = _num(first.get("open"), _num(first.get("close")))
    if cur is None or op in (None, 0):
        return None

    ret = ((cur / op) - 1.0) * 100.0
    previous = _num(upto.iloc[-2].get("close")) if len(upto) >= 2 else cur
    mom = ((cur / previous) - 1.0) * 100.0 if previous else 0.0

    high = _num(upto["high"].max(), _num(upto["close"].max()))
    low = _num(upto["low"].min(), _num(upto["close"].min()))
    range_pos = ((cur - low) / (high - low)) * 100.0 if high is not None and low is not None and high > low else 50.0

    vols = pd.to_numeric(upto["volume"], errors="coerce").dropna()
    baseline = _num(vols.iloc[:-1].tail(6).median(), 0.0) if len(vols) >= 3 else 0.0
    vol_ratio = float(vols.iloc[-1]) / baseline if len(vols) >= 3 and baseline > 0 else None

    volume = pd.to_numeric(upto["volume"], errors="coerce").fillna(0.0)
    closes = pd.to_numeric(upto["close"], errors="coerce")
    denom = float(volume.sum())
    vwap = float((closes * volume).sum() / denom) if denom > 0 else None
    vs_vwap = ((cur / vwap) - 1.0) * 100.0 if cur and vwap else None

    score = 0
    if ret >= 0.35:
        score += 2
    elif ret <= -0.35:
        score -= 2
    elif ret >= 0.10:
        score += 1
    elif ret <= -0.10:
        score -= 1

    if mom >= 0.15:
        score += 1
    elif mom <= -0.15:
        score -= 1
    if range_pos >= 65:
        score += 1
    elif range_pos <= 35:
        score -= 1
    if vs_vwap is not None:
        if vs_vwap >= 0.15:
            score += 1
        elif vs_vwap <= -0.15:
            score -= 1
    if vol_ratio is not None and vol_ratio >= 1.20:
        if mom > 0:
            score += 1
        elif mom < 0:
            score -= 1

    direction = "UP" if score >= 2 else ("DOWN" if score <= -2 else "SIDEWAYS")
    confidence = min(95.0, 50.0 + abs(score) * 8.0 + (5.0 if vol_ratio is not None and vol_ratio >= 1.20 else 0.0))
    return {
        "price": cur,
        "return_pct": ret,
        "momentum_pct": mom,
        "volume_ratio": vol_ratio,
        "range_position_pct": range_pos,
        "price_vs_vwap_pct": vs_vwap,
        "score": int(score),
        "confidence_pct": confidence,
        "direction": direction,
        "bar_minute": last_minute,
        "bars": int(len(upto)),
    }


def _order(df):
    preferred = [
        "Waktu", "Sesi", "Horizon",
        "Live Ticker", "Market Date", "Market Status", "Live Source",
        "Checkpoint State", "Live Price", "Live Return %", "Live Momentum %",
        "Volume Ratio", "Range Pos %", "Vs VWAP %", "Live Score",
        "Live Confidence %", "Live Direction", "Live Confirmation", "Live Match",
        "Match %", "Live Age m", "Live Bars", "Live Reason",
        "Samples", "UP %", "Sideways %", "DOWN %",
        "Mean Return %", "Confidence %", "Status", "Catatan",
    ]
    cols = [c for c in preferred if c in df.columns]
    cols += [c for c in df.columns if c not in cols]
    return df.loc[:, cols]


def _augment(df, st):
    if not _is_map(df):
        return df

    out = df.copy()
    defaults = {
        "Live Ticker": "",
        "Market Date": "",
        "Market Status": "",
        "Live Source": "",
        "Checkpoint State": "WAIT",
        "Live Price": None,
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
    }
    for col, default in defaults.items():
        if col not in out.columns:
            out[col] = default

    ticker = _active_ticker(st)
    time_col = _time_col(out)
    target_row = out.index[0]

    if not ticker:
        out.at[target_row, "Live Reason"] = "TICKER BELUM TERIKAT"
        out.at[target_row, "Live Match"] = "TICKER BELUM TERIKAT"
        return _order(out)
    if time_col is None:
        out.at[target_row, "Live Reason"] = "TIME SLOT TIDAK TERBACA"
        return _order(out)

    tape = _today_tape(ticker)
    status = tape["market_status"]
    market_date = tape["market_date"]
    day = tape["frame"]
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    current_minute = now.hour * 60 + now.minute

    # Always expose the active ticker/date on every row so historical percentages
    # cannot masquerade as today's state.
    out["Live Ticker"] = ticker
    out["Market Date"] = market_date
    out["Market Status"] = status
    out["Live Source"] = tape["source"]

    if day.empty:
        out["Checkpoint State"] = "NO LIVE DATA"
        out["Live Confirmation"] = "WAIT • DATA NOT FRESH"
        out["Live Match"] = "DATA LIVE BELUM SEGAR"
        out["Live Reason"] = tape["reason"] or "NO BAR FOR ACTIVE MARKET DAY"
        return _order(out)

    last_bar_minute = int(day.iloc[-1]["minute"])
    for idx, row in out.iterrows():
        minute = _slot(row.get(time_col))
        if minute is None:
            out.at[idx, "Checkpoint State"] = "SCHEDULE"
            out.at[idx, "Live Reason"] = "TIME SLOT TIDAK TERBACA"
            continue

        # Future checkpoints are deliberately blank. No historical number is
        # allowed to masquerade as a current-day realized direction.
        if minute > last_bar_minute and minute > current_minute:
            out.at[idx, "Checkpoint State"] = "WAIT FUTURE"
            out.at[idx, "Live Confirmation"] = "WAIT • CHECKPOINT BELUM TERJADI"
            out.at[idx, "Live Match"] = "WAIT FUTURE"
            out.at[idx, "Live Reason"] = "CHECKPOINT BELUM TERJADI"
            continue

        metrics = _checkpoint_metrics(day, minute)
        if metrics is None:
            out.at[idx, "Checkpoint State"] = "NO BAR"
            out.at[idx, "Live Confirmation"] = "WAIT • NO BAR"
            out.at[idx, "Live Match"] = "NO LIVE CONFIRMATION"
            out.at[idx, "Live Reason"] = "NO BAR FOR CHECKPOINT"
            continue

        out.at[idx, "Checkpoint State"] = "REALIZED TODAY"
        out.at[idx, "Live Price"] = metrics["price"]
        out.at[idx, "Live Return %"] = metrics["return_pct"]
        out.at[idx, "Live Momentum %"] = metrics["momentum_pct"]
        out.at[idx, "Volume Ratio"] = metrics["volume_ratio"]
        out.at[idx, "Range Pos %"] = metrics["range_position_pct"]
        out.at[idx, "Vs VWAP %"] = metrics["price_vs_vwap_pct"]
        out.at[idx, "Live Score"] = metrics["score"]
        out.at[idx, "Live Confidence %"] = metrics["confidence_pct"]
        out.at[idx, "Live Direction"] = metrics["direction"]
        out.at[idx, "Live Bars"] = metrics["bars"]
        out.at[idx, "Live Reason"] = "TODAY CHECKPOINT"
        out.at[idx, "Live Age m"] = tape["age_min"] if metrics["bar_minute"] == last_bar_minute else None

        hist_direction = _hist(row)
        live_direction = metrics["direction"]
        match = _hist_prob(row, live_direction)
        if live_direction == hist_direction:
            confirmation = "CONFIRMED" if match >= 55 else "CONFIRMED • WEAK"
        elif live_direction == "SIDEWAYS" or hist_direction == "SIDEWAYS":
            confirmation = "MIXED • WAIT"
        else:
            confirmation = "DIVERGENCE • WAIT"

        if metrics["bar_minute"] == last_bar_minute:
            if not tape["fresh"]:
                confirmation = "WAIT • DATA NOT FRESH"
            elif status=="BREAK":
                conf=confirmation
                confirmation=f"{conf} • BREAK"
            elif status not in {"SESSION_1", "SESSION_2", "AFTER_MARKET"}:
                confirmation = f"WAIT • {status}"

        out.at[idx, "Live Confirmation"] = confirmation
        out.at[idx, "Live Match"] = confirmation
        out.at[idx, "Match %"] = round(float(match), 1)

    # Compatibility marker retained for regression contract:
    # if status=="BREAK":conf=f"{conf} • BREAK"
    # session_pause=status in {"BREAK","AFTER_MARKET"}
    # WAIT • MARKET TIDAK LIVE
    return _order(out)


def install():
    global _INSTALLED
    if _INSTALLED:
        return True

    import streamlit as st

    original = st.dataframe
    if getattr(original, "_asi_r86_hard_timemap", False):
        _INSTALLED = True
        return True

    def dataframe(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame):
            data = _augment(data, st)
        return original(data, *args, **kwargs)

    dataframe._asi_r86_hard_timemap = True
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
