"""Hard live Time Map analysis for Streamlit/APK.

R86 patches Streamlit's dataframe renderer directly. A Time Map table can no
longer silently render historical statistics without exposing the active market
day and live confirmation state.
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
        with open(os.path.join(_BASE, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _ticker(value):
    text = str(value or "").upper().replace(".JK", "").strip()
    text = text.split("•", 1)[0].strip()
    token = re.split(r"[^A-Z0-9]+", text, maxsplit=1)[0].strip()
    return token if _TICKER_RE.fullmatch(token or "") else ""


def _active_ticker(st):
    try:
        value = st.query_params.get("ticker")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        t = _ticker(value)
        if t:
            return t
    except Exception:
        pass
    for key in ("stock_search_selection", "stock_search_query", "selected_ticker", "active_ticker", "ticker"):
        try:
            t = _ticker(st.session_state.get(key))
        except Exception:
            t = ""
        if t:
            return t
    return ""


def _looks_like_timemap(df):
    return isinstance(df, pd.DataFrame) and not df.empty and {"UP %", "DOWN %", "Samples"}.issubset(set(map(str, df.columns)))


def _time_column(df):
    for c in df.columns:
        n = str(c).strip().lower()
        if n in {"waktu", "time", "jam", "checkpoint", "slot"} or "waktu" in n or "time" in n:
            return c
    return None


def _slot_minutes(value):
    m = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _matching_row(df, time_col):
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    target = now.hour * 60 + (0 if now.minute < 30 else 30)
    exact, prior = [], []
    for idx, value in df[time_col].items():
        minute = _slot_minutes(value)
        if minute is None:
            continue
        if minute == target:
            exact.append(idx)
        elif minute <= target:
            prior.append((target - minute, idx))
    if exact:
        return exact[0]
    if prior:
        prior.sort(key=lambda x: x[0])
        return prior[0][1]
    return df.index[0] if len(df.index) else None


def _session():
    try:
        import market_session
        raw = dict(market_session.session_status(_cfg()) or {})
        return {
            "status": str(raw.get("status") or "UNKNOWN").upper(),
            "live": bool(raw.get("live")),
            "market_date": str(raw.get("market_date") or ""),
            "wib": str(raw.get("wib") or ""),
        }
    except Exception:
        now = datetime.now(ZoneInfo("Asia/Jakarta"))
        return {"status": "UNKNOWN", "live": False, "market_date": now.date().isoformat(), "wib": now.isoformat()}


def _fetch_live(ticker):
    cfg = _cfg()
    session = _session()
    out = {
        "ticker": ticker,
        "market_date": session["market_date"],
        "market_status": session["status"],
        "live_required": session["live"],
        "fresh": False,
        "bars": 0,
        "price": None,
        "return_pct": None,
        "momentum_pct": None,
        "age_min": None,
        "direction": "WAIT",
        "reason": "",
    }
    if not session["live"]:
        out["reason"] = f"MARKET {session['status']}"
        return out

    params = {"range": "5d", "interval": str(cfg.get("intraday_interval") or "30m"), "events": "history"}
    headers = {"User-Agent": str(cfg.get("user_agent") or "AI-Stock-Intelligence/1.8")}
    timeout = int(cfg.get("request_timeout_seconds") or 8)
    last_error = "PROVIDER ERROR"

    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            r = requests.get(f"https://{host}/v8/finance/chart/{ticker}.JK", params=params, timeout=timeout, headers=headers)
            r.raise_for_status()
            result = ((r.json().get("chart") or {}).get("result") or [])
            if not result:
                last_error = "NO PROVIDER RESULT"
                continue
            j = result[0]
            ts = j.get("timestamp") or []
            quote = ((j.get("indicators") or {}).get("quote") or [{}])[0]
            if not ts:
                last_error = "NO INTRADAY BARS"
                continue
            d = pd.DataFrame({
                "ts": pd.to_datetime(ts, unit="s", utc=True, errors="coerce"),
                "open": quote.get("open"), "close": quote.get("close"),
            }).dropna(subset=["ts", "close"]).sort_values("ts")
            d["wib"] = d["ts"].dt.tz_convert("Asia/Jakarta")
            d = d[d["wib"].dt.date.astype(str) == session["market_date"]].copy()
            if d.empty:
                last_error = "NO BAR FOR ACTIVE MARKET DAY"
                continue

            first, last = d.iloc[0], d.iloc[-1]
            now = pd.Timestamp(session["wib"])
            now = now.tz_localize("Asia/Jakarta") if now.tzinfo is None else now.tz_convert("Asia/Jakarta")
            age = max(0.0, (now.tz_convert("UTC") - last["ts"]).total_seconds() / 60.0)
            stale_limit = max(10, int(cfg.get("intraday_live_stale_minutes") or 25))
            op = _num(first.get("open"), _num(first.get("close")))
            cur = _num(last.get("close"))
            ret = ((cur / op) - 1.0) * 100.0 if cur and op else None
            prev = _num(d.iloc[-2].get("close")) if len(d) >= 2 else cur
            mom = ((cur / prev) - 1.0) * 100.0 if cur and prev else 0.0
            if ret is None:
                direction = "WAIT"
            elif ret > 0.20 and mom >= 0:
                direction = "UP"
            elif ret < -0.20 and mom <= 0:
                direction = "DOWN"
            else:
                direction = "SIDEWAYS"
            out.update({
                "fresh": bool(age <= stale_limit), "bars": int(len(d)), "price": cur,
                "return_pct": ret, "momentum_pct": mom, "age_min": age,
                "direction": direction, "reason": "FRESH" if age <= stale_limit else "STALE LIVE BAR",
            })
            return out
        except Exception as exc:
            last_error = type(exc).__name__.upper()
    out["reason"] = last_error
    return out


def _hist_direction(row):
    up = _num(row.get("UP %"), 0.0)
    down = _num(row.get("DOWN %"), 0.0)
    side = _num(row.get("Sideways %"), 0.0)
    if side >= max(up, down):
        return "SIDEWAYS"
    return "UP" if up >= down else "DOWN"


def _augment(df, st):
    if not _looks_like_timemap(df):
        return df
    out = df.copy()
    defaults = {
        "Live Ticker": "", "Market Date": "", "Market Status": "",
        "Live Price": None, "Live Return %": None, "Live Momentum %": None,
        "Live Direction": "WAIT", "Live Age m": None, "Live Bars": 0,
        "Live Confirmation": "WAIT", "Match %": None, "Live Reason": "",
    }
    for c, v in defaults.items():
        if c not in out.columns:
            out[c] = v

    ticker = _active_ticker(st)
    row_idx = _matching_row(out, _time_column(out)) if _time_column(out) is not None else out.index[0]
    if row_idx is None:
        return out
    if not ticker:
        out.at[row_idx, "Live Reason"] = "TICKER BELUM TERIKAT"
        return _order(out)

    live = _fetch_live(ticker)
    out.at[row_idx, "Live Ticker"] = ticker
    out.at[row_idx, "Market Date"] = live["market_date"]
    out.at[row_idx, "Market Status"] = live["market_status"]
    out.at[row_idx, "Live Price"] = live["price"]
    out.at[row_idx, "Live Return %"] = live["return_pct"]
    out.at[row_idx, "Live Momentum %"] = live["momentum_pct"]
    out.at[row_idx, "Live Direction"] = live["direction"]
    out.at[row_idx, "Live Age m"] = live["age_min"]
    out.at[row_idx, "Live Bars"] = live["bars"]
    out.at[row_idx, "Live Reason"] = live["reason"]

    if not live["live_required"]:
        out.at[row_idx, "Live Confirmation"] = "WAIT • MARKET NOT LIVE"
        return _order(out)
    if not live["fresh"]:
        out.at[row_idx, "Live Confirmation"] = "WAIT • LIVE DATA NOT FRESH"
        return _order(out)

    hist = _hist_direction(out.loc[row_idx])
    live_dir = live["direction"]
    if live_dir == hist:
        confirmation = "CONFIRMED"
        match = 100.0
    elif live_dir == "SIDEWAYS" or hist == "SIDEWAYS":
        confirmation = "MIXED • WAIT"
        match = 50.0
    else:
        confirmation = "DIVERGENCE • WAIT"
        match = 0.0
    out.at[row_idx, "Live Confirmation"] = confirmation
    out.at[row_idx, "Match %"] = match
    return _order(out)


def _order(df):
    preferred = [
        "Waktu", "Sesi", "Horizon", "Live Ticker", "Market Date", "Market Status",
        "Live Price", "Live Return %", "Live Momentum %", "Live Direction",
        "Live Confirmation", "Match %", "Live Age m", "Live Bars", "Live Reason",
        "Samples", "UP %", "Sideways %", "DOWN %", "Mean Return %", "Confidence %",
        "Status", "Catatan",
    ]
    cols = [c for c in preferred if c in df.columns]
    cols += [c for c in df.columns if c not in cols]
    return df.loc[:, cols]


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
        elif hasattr(data, "data") and isinstance(getattr(data, "data", None), pd.DataFrame):
            try:
                data = _augment(data.data, st)
            except Exception:
                pass
        return original(data, *args, **kwargs)

    dataframe._asi_r86_hard_timemap = True
    st.dataframe = dataframe
    _INSTALLED = True
    return True
