"""Production accuracy guardrails for AI Stock Intelligence.

The engine must not publish confident recommendations when the underlying
market dataset is incomplete or stale. This module intentionally contains no
UI code so the same rules can be tested and reused by worker/API surfaces.
"""
from datetime import datetime, timezone


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
    """Return a strict publication gate plus diagnostics.

    READY means the dataset is broad enough and recent enough to support
    full-universe recommendations. NOT_READY means analysis may continue for
    diagnostics, but BUY/Top-5 publication should be blocked.
    """
    universe_count = max(int(universe_count or 0), 0)
    collected_ok = max(int(collected_ok or 0), 0)
    ready_symbols = max(int(ready_symbols or 0), 0)
    price_rows = max(int(price_rows or 0), 0)
    collection_ratio = _ratio(collected_ok, universe_count)
    ready_ratio = _ratio(ready_symbols, universe_count)

    reasons = []
    if universe_count <= 0:
        reasons.append("empty universe")
    if collection_ratio < float(min_collection_ratio):
        reasons.append(f"collection coverage {collection_ratio:.1%} < {float(min_collection_ratio):.0%}")
    if ready_ratio < float(min_ready_ratio):
        reasons.append(f"ready-symbol coverage {ready_ratio:.1%} < {float(min_ready_ratio):.0%}")
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
        "ready": ready,
        "status": "READY" if ready else "DATA_NOT_READY",
        "collection_ratio": collection_ratio,
        "ready_ratio": ready_ratio,
        "price_rows": price_rows,
        "age_hours": age_hours,
        "reasons": reasons,
    }
