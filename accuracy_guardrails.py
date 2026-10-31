"""Production accuracy guardrails for AI Stock Intelligence V1.8.57.

Publication quality is based on usable persisted coverage. A partial provider
refresh is diagnostic only and must never block an otherwise healthy persisted
dataset.

This module is imported directly by ``worker_base.py``. The production worker
therefore uses it as a guaranteed bootstrap point for H-10, preliminary
Pre-Market, guarded storage housekeeping, a fast startup self-check, R47
live-precision guardrails, the R50 realised-performance feedback guard, R51
capital-preservation circuit breaker, R52-R54 portfolio/model hardening, the
R55-R60 adaptive ensemble safety layer, the R61-R67 final adaptive engine, and
R68-R72 maturity corrections for chronological OOS validation and execution EV.
"""
from datetime import datetime, timezone
import os
import sys
import threading
import time

GUARDRAIL_REVISION = "usable-v1857-r72"


def _install_live_precision_guard():
    try:
        import live_precision_patch
        live_precision_patch._WORKER_PATCHED = True
        live_precision_patch.install_core()
        print("[LIVE_PRECISION] R47 core guard installed", flush=True)
    except Exception as exc:
        print(f"[LIVE_PRECISION] install degraded: {exc!r}", flush=True)


def _install_research_feedback_guard():
    try:
        import research_feedback
        ok = bool(research_feedback.install())
        print(f"[RESEARCH_FEEDBACK] R50 guard installed={ok}", flush=True)
    except Exception as exc:
        print(f"[RESEARCH_FEEDBACK] install degraded: {exc!r}", flush=True)


def _install_capital_preservation_guard():
    try:
        import capital_preservation
        ok = bool(capital_preservation.install())
        print(f"[CAPITAL_GUARD] R51 guard installed={ok}", flush=True)
    except Exception as exc:
        print(f"[CAPITAL_GUARD] install degraded: {exc!r}", flush=True)


def _install_risk_hardening():
    try:
        import risk_hardening
        ok = bool(risk_hardening.install())
        print(f"[RISK_HARDENING] R52-R54 installed={ok}", flush=True)
    except Exception as exc:
        print(f"[RISK_HARDENING] install degraded: {exc!r}", flush=True)


def _install_ensemble_safety():
    try:
        import ensemble_safety
        ok = bool(ensemble_safety.install())
        print(f"[ENSEMBLE_SAFETY] R55-R60 installed={ok}", flush=True)
    except Exception as exc:
        print(f"[ENSEMBLE_SAFETY] install degraded: {exc!r}", flush=True)


def _install_advanced_ensemble():
    """Install real-XGBoost + temporal + regime meta layer after R55-R60."""
    try:
        import advanced_ensemble
        ok = bool(advanced_ensemble.install())
        print(f"[ADVANCED_ENSEMBLE] R61-R67 installed={ok}", flush=True)
    except Exception as exc:
        print(f"[ADVANCED_ENSEMBLE] install degraded: {exc!r}", flush=True)


def _install_advanced_maturity():
    """Correct chronology, OOS promotion thresholds and execution expected value."""
    try:
        import advanced_ensemble_maturity
        ok = bool(advanced_ensemble_maturity.install())
        print(f"[ADVANCED_MATURITY] R68-R72 installed={ok}", flush=True)
    except Exception as exc:
        print(f"[ADVANCED_MATURITY] install degraded: {exc!r}", flush=True)


_install_live_precision_guard()
_install_research_feedback_guard()
_install_capital_preservation_guard()
_install_risk_hardening()
_install_ensemble_safety()
_install_advanced_ensemble()
_install_advanced_maturity()


def _run_h10_daemon():
    try:
        time.sleep(5)
        import premarket_scheduler
        premarket_scheduler.run_forever()
    except Exception as exc:
        print(f"[PREMARKET_H10] explicit worker bootstrap error: {exc!r}", flush=True)


def _run_preliminary_daemon():
    try:
        time.sleep(6)
        import premarket_preliminary
        premarket_preliminary.run_forever()
    except Exception as exc:
        print(f"[PREMARKET_PRELIMINARY] explicit worker bootstrap error: {exc!r}", flush=True)


def _run_housekeeping_daemon():
    try:
        import storage_housekeeping
        storage_housekeeping.run_forever()
    except Exception as exc:
        print(f"[HOUSEKEEPING] explicit worker bootstrap error: {exc!r}", flush=True)


def _run_startup_selfcheck():
    try:
        import runtime_selfcheck
        result = runtime_selfcheck.run()
        print(f"[SELF_CHECK] {result}", flush=True)
    except Exception as exc:
        print(f"[SELF_CHECK] startup error: {exc!r}", flush=True)


def _start_worker_premarket_daemons():
    from runtime_profile import timemap_only
    if timemap_only():
        return
    if os.environ.get("AI_ENGINE_WORKER") != "1":
        return
    if os.path.basename(str(sys.argv[0] or "")) != "worker_base.py":
        return

    import local_runtime_health
    local_runtime_health.start_worker_monitor()
    _run_startup_selfcheck()
    live_names = {thread.name for thread in threading.enumerate()}
    if "asi-premarket-h10" not in live_names:
        threading.Thread(target=_run_h10_daemon, name="asi-premarket-h10", daemon=True).start()
    if "asi-premarket-preliminary" not in live_names:
        threading.Thread(target=_run_preliminary_daemon, name="asi-premarket-preliminary", daemon=True).start()
    if "asi-storage-housekeeping" not in live_names:
        threading.Thread(target=_run_housekeeping_daemon, name="asi-storage-housekeeping", daemon=True).start()


_start_worker_premarket_daemons()


def _ratio(a, b):
    try:
        return float(a) / float(b) if float(b) > 0 else 0.0
    except Exception:
        return 0.0


def evaluate_data_quality(*, universe_count, collected_ok, ready_symbols,
                          price_rows, last_collection=None, now=None,
                          min_collection_ratio=0.80,
                          min_ready_ratio=0.70,
                          min_price_rows=5000,
                          max_age_hours=8.0):
    """Return publication gate plus diagnostics."""
    universe_count = max(int(universe_count or 0), 0)
    collected_ok = max(int(collected_ok or 0), 0)
    ready_symbols = max(int(ready_symbols or 0), 0)
    price_rows = max(int(price_rows or 0), 0)
    collection_ratio = _ratio(collected_ok, universe_count)
    ready_ratio = _ratio(ready_symbols, universe_count)

    reasons = []
    warnings = []
    if universe_count <= 0:
        reasons.append("empty universe")
    if collection_ratio < float(min_collection_ratio):
        warnings.append(
            f"fresh collection coverage {collection_ratio:.1%} < {float(min_collection_ratio):.0%}"
        )
    if ready_ratio < float(min_ready_ratio):
        reasons.append(f"usable-symbol coverage {ready_ratio:.1%} < {float(min_ready_ratio):.0%}")
    if price_rows < int(min_price_rows):
        reasons.append(f"price rows {price_rows} < {int(min_price_rows)}")

    age_hours = None
    if last_collection:
        try:
            dt = datetime.fromisoformat(str(last_collection).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (current.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
            if age_hours > float(max_age_hours):
                reasons.append(f"collection age {age_hours:.1f}h > {float(max_age_hours):.1f}h")
        except Exception:
            reasons.append("invalid collection timestamp")
    else:
        reasons.append("missing collection timestamp")

    ready = not reasons
    return {
        "revision": GUARDRAIL_REVISION,
        "ready": ready,
        "status": "READY" if ready else "DATA_NOT_READY",
        "collection_ratio": collection_ratio,
        "ready_ratio": ready_ratio,
        "price_rows": price_rows,
        "age_hours": age_hours,
        "reasons": reasons,
        "warnings": warnings,
    }
