from datetime import datetime, timezone

from accuracy_guardrails import GUARDRAIL_REVISION, evaluate_data_quality


def test_real_runtime_451_refresh_728_ready_must_publish():
    now = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
    q = evaluate_data_quality(
        universe_count=957,
        collected_ok=451,
        ready_symbols=728,
        price_rows=86632,
        last_collection="2026-10-15T02:30:00+00:00",
        now=now,
    )
    assert GUARDRAIL_REVISION == "usable-v1857-r72"
    assert q["ready"] is True
    assert q["status"] == "READY"
    assert q["reasons"] == []
    assert q["warnings"] == ["fresh collection coverage 47.1% < 80%"]


def test_real_runtime_244_refresh_403_ready_must_block_usable_coverage():
    now = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
    q = evaluate_data_quality(
        universe_count=957,
        collected_ok=244,
        ready_symbols=403,
        price_rows=47957,
        last_collection="2026-10-15T02:30:00+00:00",
        now=now,
    )
    assert q["ready"] is False
    assert q["status"] == "DATA_NOT_READY"
    assert "usable-symbol coverage 42.1% < 70%" in q["reasons"]
    assert all("fresh collection" not in reason for reason in q["reasons"])
