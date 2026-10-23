"""Central UI semantics for mobile readability and live Time Map confirmation."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import os
import re
import sqlite3

import pandas as pd
import streamlit as st

_INSTALLED = False
_BASE = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(_BASE, "db", "market.db")


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _font(css_fg="#1f2937", weight="700"):
    return f"color:{css_fg};font-weight:{weight};"


def _direction_style(col, value):
    x = _num(value)
    if x is None:
        return _font("#9ca3af", "500")
    if col == "UP %":
        return _font("#15803d", "800" if x >= 60 else "650")
    if col == "Sideways %":
        return _font("#b45309", "800" if x >= 50 else "650")
    if col == "DOWN %":
        return _font("#b91c1c", "800" if x >= 50 else "650")
    if col in {"Mean Return %", "Live %"}:
        if x > 0:
            return _font("#15803d", "800" if x > 0.5 else "650")
        if x < 0:
            return _font("#b91c1c", "800" if x < -0.5 else "650")
        return _font("#6b7280", "600")
    if col == "Confidence %":
        return _font("#1d4ed8", "800" if x >= 70 else "650")
    if col == "Samples":
        return _font("#b45309", "800") if x < 10 else ""
    if col == "Match %":
        if x >= 70:
            return _font("#15803d", "800")
        if x < 35:
            return _font("#b91c1c", "800")
        return _font("#b45309", "700")
    return ""


def _status_style(value):
    text = str(value or "").upper().strip()
    if text == "MATCH STRONG":
        return _font("#15803d", "800")
    if text == "MATCH":
        return _font("#16a34a", "750")
    if text == "DIVERGENCE":
        return _font("#b91c1c", "850")
    if text in {"MIXED", "DATA INTRADAY BELUM CUKUP", "NO LIVE CONFIRMATION"}:
        return _font("#b45309", "750")
    return _font("#6b7280", "600")


def _normalize_ticker(value):
    text = str(value or "").upper().replace(".JK", "").strip()
    return text if re.fullmatch(r"[A-Z0-9]{2,6}", text) else ""


def _ticker_exists(ticker):
    if not ticker or not os.path.exists(_DB):
        return False
    con = None
    try:
        con = sqlite3.connect(_DB, timeout=2)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ("opportunities", "dynamic_opportunities", "prices", "intraday"):
            if table not in tables:
                continue
            row = con.execute(f"SELECT 1 FROM {table} WHERE ticker=? LIMIT 1", (ticker,)).fetchone()
            if row:
                return True
    except Exception:
        return False
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass
    return False


def _current_ticker_hint():
    candidates = []
    try:
        for key in (
            "selected_ticker", "current_ticker", "ticker", "symbol",
            "stock_search_query", "analysis_ticker", "opportunity_ticker",
            "selected_stock", "stock_symbol",
        ):
            candidates.append(st.session_state.get(key))
        # App revisions have used several names for the selected opportunity.
        # Scan scalar session values as a final fallback, but validate against
        # the local market database so labels such as READY are never mistaken
        # for ticker symbols.
        for value in st.session_state.values():
            if isinstance(value, str):
                candidates.append(value)
    except Exception:
        pass
    try:
        qp = st.query_params
        candidates.extend([qp.get("ticker"), qp.get("stock"), qp.get("symbol")])
    except Exception:
        pass
    seen = set()
    for value in candidates:
        ticker = _normalize_ticker(value)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        if _ticker_exists(ticker):
            return ticker
    return ""


def _time_column(df):
    for col in df.columns:
        name = str(col).lower().strip()
        if name in {"time", "waktu", "jam", "checkpoint", "slot"} or "time" in name or "waktu" in name:
            return col
    return None


def _current_slot_label():
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    minute = 0 if now.minute < 30 else 30
    return f"{now.hour:02d}:{minute:02d}"


def _live_metrics_for_current_ticker():
    ticker = _current_ticker_hint()
    if not ticker:
        return "", {}
    try:
        import live_precision_patch as lp
        metrics = dict(lp.live_metrics(ticker) or {})
        metrics["relative_strength_pct"] = lp.relative_strength_pct({"ticker": ticker}, metrics)
        return ticker, metrics
    except Exception:
        return ticker, {}


def _augment_time_map(df):
    """Put same-session evidence in front of historical Time Map columns."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    cols = set(map(str, df.columns))
    if not ({"UP %", "DOWN %"} <= cols and "Samples" in cols):
        return df
    time_col = _time_column(df)
    ticker, metrics = _live_metrics_for_current_ticker()
    if time_col is None or not ticker:
        return df

    out = df.copy()
    for col, default in (("Live %", None), ("Live Match", ""), ("Match %", None)):
        if col not in out.columns:
            out[col] = default

    current_slot = _current_slot_label()
    matched_index = None
    for idx, value in out[time_col].items():
        text = str(value or "")
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m and f"{int(m.group(1)):02d}:{m.group(2)}" == current_slot:
            matched_index = idx
            break

    if matched_index is not None:
        if not metrics.get("fresh"):
            out.at[matched_index, "Live Match"] = "DATA INTRADAY BELUM CUKUP"
            if "Status" in out.columns and str(out.at[matched_index, "Status"]).upper() in {"ACTIVE", "DEVELOPING"}:
                out.at[matched_index, "Status"] = "WAIT"
        else:
            try:
                import live_precision_patch as lp
                up = out.at[matched_index, "UP %"]
                down = out.at[matched_index, "DOWN %"]
                status, score = lp.map_live_match(up, down, metrics)
            except Exception:
                status, score = "MIXED", None
            live_ret = metrics.get("return_from_open_pct")
            out.at[matched_index, "Live %"] = live_ret
            out.at[matched_index, "Live Match"] = status
            out.at[matched_index, "Match %"] = score
            if "Status" in out.columns and status in {"DIVERGENCE", "MIXED"}:
                hist = str(out.at[matched_index, "Status"]).upper()
                if hist in {"ACTIVE", "DEVELOPING"}:
                    out.at[matched_index, "Status"] = "WAIT" if status == "DIVERGENCE" else "NEUTRAL"

    # Mobile decision-first order. Historical probabilities stay intact and
    # explicitly follow the live evidence instead of masquerading as live calls.
    front = [c for c in [time_col, "Sesi", "Live %", "Live Match", "Match %", "Status"] if c in out.columns]
    rest = [c for c in out.columns if c not in front]
    return out[front + rest]


def _style_dataframe(df):
    if not isinstance(df, pd.DataFrame):
        return df
    df = _augment_time_map(df)
    semantic = [c for c in [
        "Samples", "UP %", "Sideways %", "DOWN %", "Mean Return %",
        "Confidence %", "Live %", "Match %"
    ] if c in df.columns]
    status_cols = [c for c in ["Live Match"] if c in df.columns]
    if not semantic and not status_cols:
        return df
    styler = df.style
    for col in semantic:
        try:
            styler = styler.map(lambda v, c=col: _direction_style(c, v), subset=[col])
        except AttributeError:
            styler = styler.applymap(lambda v, c=col: _direction_style(c, v), subset=[col])
    for col in status_cols:
        try:
            styler = styler.map(_status_style, subset=[col])
        except AttributeError:
            styler = styler.applymap(_status_style, subset=[col])
    try:
        styler = styler.set_table_styles([
            {"selector": "th", "props": [("font-weight", "750"), ("white-space", "nowrap")]},
            {"selector": "td", "props": [("font-size", "0.93rem")]},
        ])
    except Exception:
        pass
    return styler


def format_wib(value):
    text = str(value or "").strip()
    if not text:
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo("Asia/Jakarta"))
        month = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"][local.month-1]
        return f"{local.day:02d} {month} {local.year} • {local:%H:%M:%S} WIB"
    except Exception:
        return text


def _storage_health():
    try:
        import storage_housekeeping
        return storage_housekeeping.storage_health()
    except Exception:
        return None


def _record_search(label, value):
    try:
        if str(label).strip().lower() != "cari saham":
            return
        ticker = _normalize_ticker(value)
        if ticker:
            st.session_state["stock_search_query"] = ticker
            import search_priority_refresh
            search_priority_refresh.request(ticker)
    except Exception:
        pass


def _guard_entry_window_message(body):
    text = str(body or "")
    if "Entry Window terdeteksi" not in text:
        return None
    ticker, metrics = _live_metrics_for_current_ticker()
    if not ticker:
        return None
    if not metrics.get("fresh"):
        return (
            "⏳ Entry Window historis belum mendapat konfirmasi intraday segar. "
            "Status actionable ditahan sampai data live cukup."
        )
    live_ret = _num(metrics.get("return_from_open_pct"))
    if live_ret is not None and live_ret <= -0.25:
        return (
            f"🛡️ WAIT • Entry Window historis {ticker} diblokir. "
            f"Pergerakan live dari open {live_ret:+.2f}% berlawanan dengan skenario bullish."
        )
    return None


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_dataframe = st.dataframe
    if not getattr(original_dataframe, "_asi_semantic_colors", False):
        def dataframe(data=None, *args, **kwargs):
            return original_dataframe(_style_dataframe(data), *args, **kwargs)
        dataframe._asi_semantic_colors = True
        st.dataframe = dataframe

    original_text_input = st.text_input
    if not getattr(original_text_input, "_asi_search_priority", False):
        def text_input(label, *args, **kwargs):
            value = original_text_input(label, *args, **kwargs)
            _record_search(label, value)
            return value
        text_input._asi_search_priority = True
        st.text_input = text_input

    original_info = st.info
    if not getattr(original_info, "_asi_live_entry_guard", False):
        def info(body, *args, **kwargs):
            replacement = _guard_entry_window_message(body)
            if replacement:
                return st.warning(replacement)
            return original_info(body, *args, **kwargs)
        info._asi_live_entry_guard = True
        st.info = info

    original_metric = st.metric
    if not getattr(original_metric, "_asi_format_metric", False):
        def metric(label, value, *args, **kwargs):
            if str(label).strip().lower() == "snapshot":
                value = format_wib(value)
            return original_metric(label, value, *args, **kwargs)
        metric._asi_format_metric = True
        st.metric = metric

    original_title = st.title
    if not getattr(original_title, "_asi_storage_caption", False):
        def title(value, *args, **kwargs):
            out = original_title(value, *args, **kwargs)
            if isinstance(value, str) and "AI Stock Intelligence" in value:
                health = _storage_health()
                if health:
                    st.caption(
                        f"Storage {health['status']} • DB {health['db_mb']:.1f} MB • "
                        f"cleanup {health.get('last_cleanup_label') or 'belum ada'}"
                    )
                    if health["status"] == "CRITICAL":
                        st.error("Storage mendekati batas kritis. Housekeeping otomatis akan memprioritaskan data sementara, bukan history penting.")
            return out
        title._asi_storage_caption = True
        st.title = title
