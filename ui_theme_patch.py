"""Central UI semantics for mobile readability and R47 live Time Map confirmation."""
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
    # R47: font color only. No cell background/stabilo.
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
    if text in {"MIXED", "DATA INTRADAY BELUM CUKUP"}:
        return _font("#b45309", "750")
    return _font("#6b7280", "600")


def _current_ticker_hint():
    candidates = []
    try:
        candidates.append(st.session_state.get("stock_search_query"))
    except Exception:
        pass
    try:
        qp = st.query_params
        candidates.extend([qp.get("ticker"), qp.get("stock"), qp.get("symbol")])
    except Exception:
        pass
    for value in candidates:
        text = str(value or "").upper().replace(".JK", "").strip()
        if re.fullmatch(r"[A-Z0-9]{2,6}", text):
            return text
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


def _augment_time_map(df):
    """Attach current-slot live confirmation to Time Map tables only."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    cols = set(map(str, df.columns))
    if not ({"UP %", "DOWN %"} <= cols and "Samples" in cols):
        return df
    time_col = _time_column(df)
    ticker = _current_ticker_hint()
    if time_col is None or not ticker:
        return df
    try:
        import live_precision_patch as lp
        metrics = lp.live_metrics(ticker)
        metrics["relative_strength_pct"] = lp.relative_strength_pct({"ticker": ticker}, metrics)
    except Exception:
        return df
    out = df.copy()
    if "Live %" not in out.columns:
        out["Live %"] = None
    if "Live Match" not in out.columns:
        out["Live Match"] = ""
    if "Match %" not in out.columns:
        out["Match %"] = None
    current_slot = _current_slot_label()
    matched_index = None
    for idx, value in out[time_col].items():
        text = str(value or "")
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m and f"{int(m.group(1)):02d}:{m.group(2)}" == current_slot:
            matched_index = idx
            break
    if matched_index is None:
        return out
    if not metrics.get("fresh"):
        out.at[matched_index, "Live Match"] = "DATA INTRADAY BELUM CUKUP"
        return out
    up = out.at[matched_index, "UP %"]
    down = out.at[matched_index, "DOWN %"]
    status, score = lp.map_live_match(up, down, metrics)
    out.at[matched_index, "Live %"] = metrics.get("return_from_open_pct")
    out.at[matched_index, "Live Match"] = status
    out.at[matched_index, "Match %"] = score
    return out


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
        ticker = str(value or "").upper().replace(".JK", "").strip()
        if re.fullmatch(r"[A-Z0-9]{2,6}", ticker):
            import live_precision_patch
            live_precision_patch.record_search_priority(ticker)
    except Exception:
        pass


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
