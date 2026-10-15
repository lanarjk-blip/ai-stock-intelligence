from datetime import datetime, timezone, timedelta

import hybrid_worker as hw

WIB = timezone(timedelta(hours=7))


def test_market_window_active_during_weekday_morning():
    status = hw.market_window(datetime(2026, 10, 19, 8, 0, tzinfo=WIB))
    assert status["active"] is True
    assert status["start"].hour == 7 and status["start"].minute == 45
    assert status["end"].hour == 16 and status["end"].minute == 30


def test_market_window_not_active_before_0745():
    status = hw.market_window(datetime(2026, 10, 19, 7, 44, tzinfo=WIB))
    assert status["active"] is False
    assert hw.next_start(status["now"]) == datetime(2026, 10, 19, 7, 45, tzinfo=WIB)


def test_next_start_skips_weekend():
    now = datetime(2026, 10, 16, 17, 0, tzinfo=WIB)  # Friday after close
    assert hw.next_start(now) == datetime(2026, 10, 19, 7, 45, tzinfo=WIB)


def test_next_start_skips_verified_christmas_holiday():
    now = datetime(2026, 12, 24, 17, 0, tzinfo=WIB)
    assert hw.next_start(now) == datetime(2026, 12, 28, 7, 45, tzinfo=WIB)


def test_detect_device_recognizes_termux_and_explicit_override():
    assert hw.detect_device({"PREFIX": "/data/data/com.termux/files/usr"}) == "hp"
    assert hw.detect_device({"ASI_WORKER_DEVICE": "laptop", "ANDROID_ROOT": "/system"}) == "laptop"


def test_hp_child_environment_enables_hybrid_and_battery_aware_profile():
    env = hw.child_environment(
        "hp",
        {"DATABASE_URL": "postgresql://unit", "OPENBLAS_NUM_THREADS": "1"},
    )
    assert env["AI_ENGINE_WORKER"] == "1"
    assert env["ASI_HYBRID_WORKER"] == "1"
    assert env["ASI_WORKER_DEVICE"] == "hp"
    assert env["ASI_DEVICE_PROFILE"] == "HP_FAST"
    assert env["OMP_NUM_THREADS"] == "2"
    assert env["OPENBLAS_NUM_THREADS"] == "1"  # explicit user value preserved
    assert env["ASI_HYBRID_CLAIM_DELAY_SECONDS"] == "8"


def test_laptop_gets_full_profile_and_shorter_startup_bias():
    env = hw.child_environment("laptop", {"DATABASE_URL": "postgresql://unit"})
    assert env["ASI_DEVICE_PROFILE"] == "FULL"
    assert env["ASI_HYBRID_CLAIM_DELAY_SECONDS"] == "2"
