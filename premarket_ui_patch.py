"""Streamlit compatibility patch for resilient Pre-Market runtime.

The legacy app renderer assumes every non-NO_TRADE Pre-Market row is already
locked. Production now has a real PRELIMINARY state. This patch also gives the
UI a lightweight self-healing path: opening the Pre-Market panel materializes a
PRELIMINARY run from the latest opportunity snapshot when appropriate.
"""
from __future__ import annotations

import os
import re
import sqlite3

_BASE = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(_BASE, "db", "market.db")
_WORKER_LOCK = "/tmp/ai_stock_intelligence_worker.singleton.lock"


def _latest_status() -> str:
    try:
        con = sqlite3.connect(_DB, timeout=3)
        row = con.execute(
            "SELECT status FROM premarket_runs ORDER BY market_date DESC, generated_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        return str(row[0] if row else "").upper().strip()
    except Exception:
        return ""


def _remote_worker_identity():
    try:
        con = sqlite3.connect(_DB, timeout=3)
        rows = con.execute(
            "SELECT key,value FROM state WHERE key IN "
            "('remote_worker_runtime_build','remote_worker_generation','remote_worker_pid','remote_worker_heartbeat_at')"
        ).fetchall()
        con.close()
        state = {str(k): str(v or "") for k, v in rows}
        build = state.get("remote_worker_runtime_build", "")
        generation = state.get("remote_worker_generation", "")
        if not build:
            return None
        try:
            pid = int(state.get("remote_worker_pid") or 0)
        except Exception:
            pid = 0
        return {
            "pid": pid,
            "generation": generation or "?",
            "build": build,
            "remote": True,
            "heartbeat_at": state.get("remote_worker_heartbeat_at", ""),
        }
    except Exception:
        return None


def _live_worker_identity():
    try:
        with open(_WORKER_LOCK, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        fields = {}
        for token in raw.split():
            if "=" in token:
                key, value = token.split("=", 1)
                fields[key] = value
        pid = int(fields.get("pid") or 0)
        if pid > 0 and os.path.exists(f"/proc/{pid}"):
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            if "worker_base.py" in cmd:
                return {
                    "pid": pid,
                    "generation": fields.get("generation", "?"),
                    "build": fields.get("build", "?"),
                    "remote": False,
                }
    except Exception:
        pass
    return _remote_worker_identity()


def _ensure_premarket_on_open():
    try:
        import premarket_preliminary
        return premarket_preliminary.ensure_on_open()
    except Exception as exc:
        return {"status": "ERROR", "detail": repr(exc)}


def install():
    import streamlit as st

    if getattr(st.success, "_asi_premarket_preliminary_patch", False):
        return

    original_success = st.success
    original_info = st.info
    original_caption = st.caption
    original_subheader = st.subheader

    def success(body, *args, **kwargs):
        text = str(body)
        if _latest_status() == "PRELIMINARY" and (
            "Pre-Market pick sudah dikunci" in text
            or "pick terkunci untuk sesi" in text
            or "pick sudah dikunci" in text
        ):
            return original_info(
                "🟡 **PRELIMINARY • kandidat awal sesi berikutnya.** "
                "Daftar ini masih boleh berubah. Refresh diprioritaskan menjelang pembukaan dan "
                "hasil final dikunci pukul **08:50 WIB (H-10)**.",
                *args,
                **kwargs,
            )
        return original_success(body, *args, **kwargs)

    def caption(body, *args, **kwargs):
        text = str(body)
        if _latest_status() == "PRELIMINARY" and "Daftar ini adalah pilihan yang dikunci" in text:
            body = (
                "Daftar ini masih PRELIMINARY untuk sesi berikutnya. Kandidat awal dapat berubah "
                "sampai refresh terakhir; snapshot final H-10 dikunci pukul 08:50 WIB dan setelah "
                "itu tidak boleh diedit berdasarkan hasil pasar."
            )
            text = str(body)

        if text.startswith("Runtime build "):
            identity = _live_worker_identity()
            if identity:
                location = "Render" if identity.get("remote") else "Local"
                pid_text = f" PID {identity['pid']}" if int(identity.get("pid") or 0) > 0 else ""
                replacement = (
                    f"Runtime build {identity['build']} • Worker {location}{pid_text} "
                    f"• Gen {identity['generation']}"
                )
                body = re.sub(r"^Runtime build [^•]+", replacement, text, count=1)
        return original_caption(body, *args, **kwargs)

    def subheader(body, *args, **kwargs):
        if str(body).strip() == "🌅 Pre-Market Top Picks":
            result = _ensure_premarket_on_open()
            try:
                st.session_state["_asi_premarket_on_open"] = result
            except Exception:
                pass
            rendered = original_subheader(body, *args, **kwargs)
            status = str((result or {}).get("status") or "")
            if status == "NO_SNAPSHOT":
                original_info(
                    "⏳ **Pre-Market sedang memulihkan snapshot.** Worker belum memiliki snapshot "
                    "Opportunity yang bisa dipakai; panel tidak membuat BUY palsu dan akan terisi "
                    "setelah snapshot pertama tersedia."
                )
            elif status == "ERROR":
                original_info("⚠️ Pre-Market self-heal gagal; worker tetap menjadi jalur utama.")
            return rendered
        return original_subheader(body, *args, **kwargs)

    success._asi_premarket_preliminary_patch = True
    caption._asi_premarket_preliminary_patch = True
    subheader._asi_premarket_on_open_patch = True
    st.success = success
    st.caption = caption
    st.subheader = subheader
