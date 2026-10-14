"""Small Streamlit copy patch for PRELIMINARY Pre-Market state.

The legacy app renderer assumes every non-NO_TRADE Pre-Market row is already
locked. H-10 production now has a real PRELIMINARY state, so this patch changes
only the misleading lock wording while leaving all other Streamlit calls alone.
"""
from __future__ import annotations

import os
import sqlite3


def _latest_status() -> str:
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "market.db")
    try:
        con = sqlite3.connect(db, timeout=3)
        row = con.execute(
            "SELECT status FROM premarket_runs ORDER BY market_date DESC, generated_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        return str(row[0] if row else "").upper().strip()
    except Exception:
        return ""


def install():
    import streamlit as st

    if getattr(st.success, "_asi_premarket_preliminary_patch", False):
        return

    original_success = st.success
    original_info = st.info
    original_caption = st.caption

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
        return original_caption(body, *args, **kwargs)

    success._asi_premarket_preliminary_patch = True
    caption._asi_premarket_preliminary_patch = True
    st.success = success
    st.caption = caption
