"""Production accuracy guardrails for AI Stock Intelligence V1.8.55.

Publication quality is based on usable persisted coverage. A partial provider
refresh is diagnostic only and must never block an otherwise healthy persisted
dataset.

This module is imported directly by ``worker_base.py``. The production worker
therefore uses it as a guaranteed bootstrap point for H-10, preliminary
Pre-Market, guarded storage housekeeping, and a fast startup self-check.
"""
from datetime import datetime, timezone
import os
import sys
import threading
import time

# Keep the data-quality algorithm revision stable. R45 adds runtime/UI/storage
# helpers but does not change the publication-gate mathematics.
GUARDRAIL_REVISION = "usable-v1852-r2"


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
    """Start production helpers exactly once in the real worker.

    CI smoke tests also set AI_ENGINE_WORKER=1, so the argv guard prevents
    background scheduling from leaking into inline validation processes.
    """
    if os.environ.get("AI_ENGINE_WORKER") != "1":
        return
    if os.path.basename(str(sys.argv[0] or "")) != "worker_base.py":
        return

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
    """Return publication gate plus diagnostics.

    Fresh collection coverage is intentionally warning-only. Blocking criteria
    are usable persisted-symbol coverage, row count and data freshness.
    """
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
