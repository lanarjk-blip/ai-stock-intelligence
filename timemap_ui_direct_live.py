"""Direct live Time Map fallback for the Streamlit/APK UI.

R85 removes the last implicit dependency on a hidden ticker hint. The active
selected ticker is resolved from the deep-link query parameter, stock-search
selection, stock-search query, and finally the legacy theme hint. Live evidence
is fetched only for that ticker, filtered to the active IDX market date, and
shown next to historical Time Map statistics. Previous-session bars can never
satisfy a new market day's live state.
"""
from __future__ import annotations

import math
import re

import pandas as pd

_INSTALLED = False
_TICKER_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _ticker_token(value):
    text = str(value or "").upper().replace(".JK", "").strip()
    if not text:
        return ""
    # Search selection labels use: TICKER • Company • match xx%
    text = text.split("•", 1)[0].strip()
    # Query text may contain extra words; only trust a clean first token.
    token = re.split(r"[^A-Z0-9]+", text, maxsplit=1)[0].strip()
    return token if _TICKER_RE.fullmatch(token or "") else ""


def _active_ticker(theme=None):
    """Resolve the exact ticker currently opened in the UI.

    Order matters: an explicit deep-link is authoritative, then the selectbox
    choice, then the search query. Legacy theme/session hints are only fallback.
    """
    try:
        import streamlit as st

        try:
            qp = st.query_params
            value = qp.get("ticker")
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            ticker = _ticker_token(value)
            if ticker:
                return ticker
        except Exception:
            pass

        for key in (
            "stock_search_selection",
            "stock_search_query",
            "selected_ticker",
            "active_ticker",
            "ticker",
        ):
            try:
                ticker = _ticker_token(st.session_state.get(key))
            except Exception:
                ticker = ""
            if ticker:
                return ticker
    except Exception:
        pass

    if theme is not None:
        try:
            ticker = _ticker_token(theme._current_ticker_hint())
            if ticker:
                return ticker
        except Exception:
            pass
    return ""


def _direct_metrics(ticker):
    import app
    import market_session

    session = dict(market_session.session_status(app.CFGD) or {})
    market_date = str(session.get("market_date") or "")
    out = {
        "ticker": _ticker_token(ticker),
        "live_required": bool(session.get("live")),
        "fresh": False,
        "bars": 0,
        "market_date": market_date,
        "last_ts": "",
        "age_minutes": None,
        "open_price": None,
        "current_price": None,
        "return_from_open_pct": None,
        "momentum_pct": None,
        "volume_ratio": None,
        "range_position": None,
        "source": "UI_DIRECT",
    }
    if not out["ticker"] or not out["live_required"]:
        return out

    try:
        value = app.fetch_chart_recover(
            out["ticker"],
            max(2, int(app.CFGD.get("intraday_refresh_period_days", 5))),
            app.CFGD.get("intraday_interval", "30m"),
        )
        if isinstance(value, tuple):
            df = next((v for v in value if isinstance(v, pd.DataFrame)), pd.DataFrame())
        else:
            df = value if isinstance(value, pd.DataFrame) else pd.DataFrame()
        if df.empty or "ts" not in df.columns or "close" not in df.columns:
            return out

        d = df.copy()
        d["ts"] = pd.to_datetime(d["ts"], utc=True, errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            if col in d.columns:
                d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d.dropna(subset=["ts", "close"]).sort_values("ts")
        if d.empty:
            return out

        d["wib"] = d["ts"].dt.tz_convert("Asia/Jakarta")
        # Absolute market-day boundary: yesterday can never become today's live evidence.
        d = d[d["wib"].dt.date.astype(str) == market_date].copy()
        if d.empty:
            return out

        now = pd.Timestamp(session.get("wib"))
        if now.tzinfo is None:
            now = now.tz_localize("Asia/Jakarta")
        else:
            now = now.tz_convert("Asia/Jakarta")

        last = d.iloc[-1]
        first = d.iloc[0]
        age = max(0.0, (now.tz_convert("UTC") - last["ts"]).total_seconds() / 60.0)
        stale_limit = max(10, int(app.CFGD.get("intraday_live_stale_minutes", 30)))
        op = _num(first.get("open"), _num(first.get("close")))
        cur = _num(last.get("close"))
        ret = ((cur / op) - 1.0) * 100.0 if cur and op else None

        if len(d) >= 2:
            prev = _num(d.iloc[-2].get("close"))
            mom = ((cur / prev) - 1.0) * 100.0 if cur and prev else 0.0
        else:
            mom = 0.0

        vols = (
            pd.to_numeric(d.get("volume"), errors="coerce").dropna()
            if "volume" in d.columns
            else pd.Series(dtype=float)
        )
        baseline = _num(vols.iloc[:-1].tail(6).median(), 0.0) if len(vols) >= 3 else 0.0
        vr = float(vols.iloc[-1]) / baseline if len(vols) >= 3 and baseline > 0 else None
        hi = _num(d["high"].max()) if "high" in d.columns else cur
        lo = _num(d["low"].min()) if "low" in d.columns else cur
        pos = (
            (cur - lo) / (hi - lo)
            if cur is not None and hi is not None and lo is not None and hi > lo
            else 0.5
        )
        out.update(
            {
                "fresh": bool(age <= stale_limit),
                "bars": int(len(d)),
                "last_ts": last["ts"].isoformat(),
                "age_minutes": age,
                "open_price": op,
                "current_price": cur,
                "return_from_open_pct": ret,
                "momentum_pct": mom,
                "volume_ratio": vr,
                "range_position": pos,
            }
        )
        return out
    except Exception:
        return out


def _slot_minutes(value):
    m = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _matching_row(frame, time_col):
    now = pd.Timestamp.now(tz="Asia/Jakarta")
    target = now.hour * 60 + (0 if now.minute < 30 else 30)
    exact = []
    previous = []
    for idx, value in frame[time_col].items():
        minute = _slot_minutes(value)
        if minute is None:
            continue
        if minute == target:
            exact.append(idx)
        elif minute <= target:
            previous.append((target - minute, idx))
    if exact:
        return exact[0]
    if previous:
        previous.sort(key=lambda item: item[0])
        return previous[0][1]
    return None


def install():
    global _INSTALLED
    if _INSTALLED:
        return True

    import live_precision_patch as lp
    import ui_theme_patch as theme

    original = theme._augment_time_map
    if getattr(original, "_asi_direct_live_r85", False):
        _INSTALLED = True
        return True

    def augmented(df):
        out = original(df)
        if not isinstance(out, pd.DataFrame) or out.empty:
            return out

        cols = set(map(str, out.columns))
        if not ({"UP %", "DOWN %", "Samples"} <= cols):
            return out

        # Always expose the live contract columns on a Time Map table. This makes
        # a failed live binding visible instead of silently showing historical data.
        defaults = {
            "Live %": None,
            "Live Match": "",
            "Match %": None,
            "Live Price": None,
            "Live Age m": None,
            "Live Source": "",
            "Market Date": "",
            "Live Ticker": "",
        }
        for name, default in defaults.items():
            if name not in out.columns:
                out[name] = default

        ticker = _active_ticker(theme)
        time_col = theme._time_column(out)
        if not ticker:
            out.iloc[0, out.columns.get_loc("Live Match")] = "TICKER BELUM TERIKAT"
            return theme._decision_first(out)
        if time_col is None:
            out.iloc[0, out.columns.get_loc("Live Match")] = "TIME SLOT TIDAK TERBACA"
            out.iloc[0, out.columns.get_loc("Live Ticker")] = ticker
            return theme._decision_first(out)

        matched = _matching_row(out, time_col)
        if matched is None:
            out.iloc[0, out.columns.get_loc("Live Match")] = "CHECKPOINT BELUM TERSEDIA"
            out.iloc[0, out.columns.get_loc("Live Ticker")] = ticker
            return theme._decision_first(out)

        metrics = _direct_metrics(ticker)
        out.at[matched, "Live Ticker"] = ticker
        out.at[matched, "Market Date"] = metrics.get("market_date") or ""
        out.at[matched, "Live Source"] = "DIRECT PROVIDER"
        out.at[matched, "Live Price"] = metrics.get("current_price")
        out.at[matched, "Live Age m"] = metrics.get("age_minutes")

        if not metrics.get("live_required"):
            out.at[matched, "Live Match"] = "MARKET TIDAK LIVE"
            return theme._decision_first(out)
        if not metrics.get("fresh"):
            out.at[matched, "Live Match"] = "DATA LIVE BELUM SEGAR"
            return theme._decision_first(out)

        up = out.at[matched, "UP %"]
        down = out.at[matched, "DOWN %"]
        metrics["relative_strength_pct"] = lp.relative_strength_pct({"ticker": ticker}, metrics)
        status, score = lp.map_live_match(up, down, metrics)
        out.at[matched, "Live %"] = metrics.get("return_from_open_pct")
        out.at[matched, "Live Match"] = status
        out.at[matched, "Match %"] = score
        return theme._decision_first(out)

    augmented._asi_direct_live = True
    augmented._asi_direct_live_r85 = True
    theme._augment_time_map = augmented
    _INSTALLED = True
    return True
