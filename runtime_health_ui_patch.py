"""UI safety patch for stale/missing external runtime heartbeat.

External-mode Streamlit is a read-only replica of the authoritative Render
worker. If that worker stops heartbeating, cached recommendations must not keep
looking actionable merely because DATA_QUALITY was READY in the last snapshot.
"""
from __future__ import annotations

import runtime_health


def install():
    import streamlit as st
    import dynamic_opportunity_ui
    import premarket_ui_patch

    if getattr(dynamic_opportunity_ui._quality_gate_state, "_asi_runtime_health_patch", False):
        return

    original_quality = dynamic_opportunity_ui._quality_gate_state
    original_premarket_ensure = premarket_ui_patch._ensure_premarket_on_open
    original_subheader = st.subheader
    original_success = st.success

    def quality_gate_state():
        base = dict(original_quality() or {})
        health = runtime_health.remote_runtime_health()
        if health.get("required") and not health.get("ready"):
            reason = str(health.get("reason") or health.get("status") or "remote runtime unavailable")
            previous = str(base.get("reasons") or "").strip()
            base.update(
                {
                    "known": True,
                    "ready": False,
                    "status": str(health.get("status") or "RUNTIME_STALE"),
                    "reasons": " | ".join(x for x in (previous, reason) if x),
                    "runtime_health": health,
                }
            )
        else:
            base["runtime_health"] = health
        return base

    quality_gate_state._asi_runtime_health_patch = True
    dynamic_opportunity_ui._quality_gate_state = quality_gate_state

    def ensure_premarket_on_open():
        health = runtime_health.remote_runtime_health()
        if health.get("required") and not health.get("ready"):
            return {
                "status": str(health.get("status") or "RUNTIME_STALE"),
                "detail": str(health.get("reason") or "remote runtime unavailable"),
                "runtime_health": health,
            }
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
                st.warning(
                    "🛡️ **RUNTIME TIDAK SEGAR • Pre-Market dibekukan sebagai data lama.** "
                    f"{reason}. Sistem tidak membuat atau meng-upgrade kandidat sampai worker kembali sehat."
                )
        return rendered

    def success(body, *args, **kwargs):
        text = str(body)
        health = runtime_health.remote_runtime_health()
        if (
            health.get("required")
            and not health.get("ready")
            and "Pre-Market" in text
        ):
            return st.warning(
                "🛡️ **RUNTIME TIDAK SEGAR • status Pre-Market ini tidak actionable.** "
                + str(health.get("reason") or "worker Render tidak memiliki heartbeat segar")
            )
        return original_success(body, *args, **kwargs)

    st.subheader = subheader
    st.success = success
