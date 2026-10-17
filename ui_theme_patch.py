"""Central UI semantics for mobile readability.

Applied as a runtime patch from ai_stock_ask import so legacy app.py can keep
its existing rendering flow while tables, timestamps and storage status become
consistent across the Streamlit UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import os
import sqlite3

import pandas as pd
import streamlit as st

_INSTALLED = False


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _cell(css_bg, css_fg="#1f2937", weight="700"):
    return f"background-color:{css_bg};color:{css_fg};font-weight:{weight};"


def _direction_style(col, value):
    x = _num(value)
    if x is None:
        return _cell("#f3f4f6", "#9ca3af", "500")
    if col == "UP %":
        if x >= 60: return _cell("#c8e6c9", "#166534")
        if x >= 40: return _cell("#e8f5e9", "#166534")
        return _cell("#f4fbf5", "#4b5563", "600")
    if col == "Sideways %":
        if x >= 50: return _cell("#ffecb3", "#92400e")
        if x >= 30: return _cell("#fff8e1", "#92400e")
        return _cell("#fffdf5", "#6b7280", "600")
    if col == "DOWN %":
        if x >= 50: return _cell("#ffcdd2", "#991b1b")
        if x >= 30: return _cell("#ffebee", "#991b1b")
        return _cell("#fff7f7", "#6b7280", "600")
    if col == "Mean Return %":
        if x > 0.5: return _cell("#d7f3dc", "#166534")
        if x > 0: return _cell("#eef9f0", "#166534", "600")
        if x < -0.5: return _cell("#ffd7db", "#991b1b")
        if x < 0: return _cell("#fff0f1", "#991b1b", "600")
        return _cell("#f3f4f6", "#6b7280", "600")
    if col == "Confidence %":
        if x >= 70: return _cell("#dbeafe", "#1d4ed8")
        if x >= 50: return _cell("#eff6ff", "#1d4ed8", "600")
        return _cell("#f3f4f6", "#6b7280", "600")
    if col == "Samples":
        if x < 10: return _cell("#fff3cd", "#92400e", "700")
        return ""
    return ""


def _style_dataframe(df):
    if not isinstance(df, pd.DataFrame):
        return df
    semantic = [c for c in ["Samples", "UP %", "Sideways %", "DOWN %", "Mean Return %", "Confidence %"] if c in df.columns]
    if not semantic:
        return df
    styler = df.style
    for col in semantic:
        try:
            styler = styler.map(lambda v, c=col: _direction_style(c, v), subset=[col])
        except AttributeError:
            styler = styler.applymap(lambda v, c=col: _direction_style(c, v), subset=[col])
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
