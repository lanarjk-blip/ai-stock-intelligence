"""UI safety patch for stale/missing external runtime heartbeat.

External-mode Streamlit is a read-only replica of the authoritative worker.
R82 also installs the Time Map dataframe patch directly in the UI process so
live confirmation cannot depend on an implicit startup side effect.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import runtime_health

_BASE = os.path.dirname(os.path.abspath(__file__))
_WIB = ZoneInfo("Asia/Jakarta")
_MONTHS_ID = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"Mei",6:"Jun",7:"Jul",8:"Agu",9:"Sep",10:"Okt",11:"Nov",12:"Des"}


def _parse_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_WIB)
        return dt.astimezone(_WIB)
    except Exception:
        return None


def _format_wib(dt):
    if dt is None:
        return "Belum ada"
    local = dt.astimezone(_WIB)
    return f"{local.day:02d} {_MONTHS_ID.get(local.month,local.month)} {local.year} • {local:%H:%M:%S} WIB"


def _latest_persisted_update():
    db_path = os.path.join(_BASE, "db", "market.db")
    candidates = []
    if not os.path.exists(db_path):
        return None
    con = None
    try:
        con = sqlite3.connect(db_path, timeout=3)
        state_keys = ("engine_last_run","snapshot_ready_at","last_intraday_collection","last_collection","last_intraday_attempt","active_model_trained_at","walk_forward_at")
        placeholders = ",".join("?" for _ in state_keys)
        rows = con.execute(f"SELECT value FROM state WHERE key IN ({placeholders})", state_keys).fetchall()
        for (value,) in rows:
            parsed = _parse_timestamp(value)
            if parsed is not None:
                candidates.append(parsed)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ("dynamic_opportunities","outlook"):
            if table in tables:
                try:
                    value = con.execute(f"SELECT MAX(generated_at) FROM {table}").fetchone()[0]
                    parsed = _parse_timestamp(value)
                    if parsed is not None:
                        candidates.append(parsed)
                except Exception:
                    pass
    except Exception:
        return None
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass
    return max(candidates) if candidates else None


def _market_status():
    now_wib = datetime.now(_WIB)
    try:
        cfg_path = os.path.join(_BASE, "config.json")
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        import market_session
        status = dict(market_session.session_status(cfg) or {})
        raw = str(status.get("status") or "UNKNOWN").upper()
        if bool(status.get("live")):
            return "🟢 Market Live", raw
        if "PRE" in raw:
            return "🟡 Pre-Market", raw
        if "BREAK" in raw:
            return "🟡 Market Break", raw
        return "⚫ Market Closed", raw
    except Exception:
        return ("⚫ Market Closed", "UNKNOWN") if now_wib.weekday() >= 5 else ("⚪ Market Status", "UNKNOWN")


def _render_refresh_status(st):
    now_wib = datetime.now(_WIB)
    market_label, market_raw = _market_status()
    latest = _latest_persisted_update()
    left, right = st.columns([5,1])
    with left:
        st.markdown(f"**{market_label}**")
        raw_note = f" • status {market_raw}" if market_raw and market_raw != "UNKNOWN" else ""
        st.caption(f"Sekarang: {_format_wib(now_wib)} • Update terakhir: {_format_wib(latest)}{raw_note} • Sinkronisasi Engine ↔ Neon")
    with right:
        if st.button("🔄 Refresh", key="asi_manual_refresh_top", use_container_width=True):
            try:
                import replica_runtime
                replica_runtime.pull_ui_once()
            except Exception:
                pass
            try:
                st.cache_data.clear()
            except Exception:
                pass
            st.session_state["asi_manual_refresh_at"] = now_wib.isoformat()
            st.rerun()


def install():
    import streamlit as st
    import dynamic_opportunity_ui
    import premarket_ui_patch

    # R82: install Time Map dataframe/search hooks explicitly in the UI process.
    try:
        import ui_theme_patch
        ui_theme_patch.install()
    except Exception as exc:
        print(f"[R82_TIME_MAP_UI] install degraded: {exc!r}", flush=True)

    if getattr(dynamic_opportunity_ui._quality_gate_state, "_asi_runtime_health_patch", False):
        return

    original_quality = dynamic_opportunity_ui._quality_gate_state
    original_premarket_ensure = premarket_ui_patch._ensure_premarket_on_open
    original_subheader = st.subheader
    original_success = st.success
    original_title = st.title

    def quality_gate_state():
        base = dict(original_quality() or {})
        health = runtime_health.remote_runtime_health()
        if health.get("required") and not health.get("ready"):
            reason = str(health.get("reason") or health.get("status") or "remote runtime unavailable")
            previous = str(base.get("reasons") or "").strip()
            base.update({"known":True,"ready":False,"status":str(health.get("status") or "RUNTIME_STALE"),"reasons":" | ".join(x for x in (previous,reason) if x),"runtime_health":health})
        else:
            base["runtime_health"] = health
        return base

    quality_gate_state._asi_runtime_health_patch = True
    dynamic_opportunity_ui._quality_gate_state = quality_gate_state

    def ensure_premarket_on_open():
        health = runtime_health.remote_runtime_health()
        if health.get("required") and not health.get("ready"):
            return {"status":str(health.get("status") or "RUNTIME_STALE"),"detail":str(health.get("reason") or "remote runtime unavailable"),"runtime_health":health}
        return original_premarket_ensure()

    premarket_ui_patch._ensure_premarket_on_open = ensure_premarket_on_open

    def subheader(body, *args, **kwargs):
        rendered = original_subheader(body, *args, **kwargs)
        if str(body).strip() == "🌅 Pre-Market Top Picks":
            health = runtime_health.remote_runtime_health()
            if health.get("required") and not health.get("ready"):
                age = health.get("age_seconds")
                age_text = "belum ada heartbeat" if age is None else f"heartbeat terakhir {age:.0f} detik lalu"
                reason = str(health.get("reason") or age_text)
                st.warning("🛡️ **RUNTIME TIDAK SEGAR • Pre-Market dibekukan sebagai data lama.** "+reason+". Sistem tidak membuat atau meng-upgrade kandidat sampai worker kembali sehat.")
        return rendered

    def success(body, *args, **kwargs):
        text = str(body)
        health = runtime_health.remote_runtime_health()
        if health.get("required") and not health.get("ready") and "Pre-Market" in text:
            return st.warning("🛡️ **RUNTIME TIDAK SEGAR • status Pre-Market ini tidak actionable.** "+str(health.get("reason") or "worker tidak memiliki heartbeat segar"))
        return original_success(body, *args, **kwargs)

    def title(body, *args, **kwargs):
        rendered = original_title(body, *args, **kwargs)
        if str(body).startswith("📈 AI Stock Intelligence V"):
            _render_refresh_status(st)
        return rendered

    title._asi_refresh_status_patch = True
    st.subheader = subheader
    st.success = success
    st.title = title
