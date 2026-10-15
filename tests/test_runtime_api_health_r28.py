from datetime import datetime, timedelta, timezone

import runtime_api


def _heartbeat(at):
    return (123, 'gen', 'build', at, 'bridge')


def _cfg():
    return {
        'runtime_heartbeat_max_age_seconds': 180,
        'engine_progress_max_age_seconds': 1200,
        'engine_result_max_age_minutes': 90,
    }


def test_runtime_health_snapshot_ready_when_heartbeat_and_engine_result_are_fresh():
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    state = {
        'engine_cycle_status': 'IDLE',
        'engine_last_run': (now - timedelta(minutes=10)).isoformat(),
        'engine_progress_at': (now - timedelta(minutes=10)).isoformat(),
        'engine_progress_stage': 'DONE',
    }
    out = runtime_api.runtime_health_snapshot(_heartbeat(now - timedelta(seconds=20)), state, now=now, cfg=_cfg())
    assert out['ok'] is True
    assert out['status'] == 'RUNTIME_READY'
    assert out['heartbeat_age_seconds'] == 20
    assert out['engine']['ready'] is True


def test_runtime_health_snapshot_fails_when_worker_heartbeat_is_missing():
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    state = {
        'engine_cycle_status': 'IDLE',
        'engine_last_run': (now - timedelta(minutes=5)).isoformat(),
    }
    out = runtime_api.runtime_health_snapshot(None, state, now=now, cfg=_cfg())
    assert out['ok'] is False
    assert out['status'] == 'RUNTIME_MISSING'


def test_runtime_health_snapshot_fails_when_engine_stalls_despite_fresh_heartbeat():
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    state = {
        'engine_cycle_status': 'RUNNING',
        'engine_last_run': (now - timedelta(minutes=30)).isoformat(),
        'engine_progress_at': (now - timedelta(minutes=25)).isoformat(),
        'engine_progress_stage': 'FULL_COLLECT',
    }
    out = runtime_api.runtime_health_snapshot(_heartbeat(now - timedelta(seconds=10)), state, now=now, cfg=_cfg())
    assert out['ok'] is False
    assert out['status'] == 'ENGINE_STALLED'
    assert out['heartbeat_age_seconds'] == 10
    assert out['engine']['ready'] is False


def test_runtime_health_snapshot_fails_when_heartbeat_is_stale_before_engine_check():
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    state = {
        'engine_cycle_status': 'IDLE',
        'engine_last_run': (now - timedelta(minutes=5)).isoformat(),
    }
    out = runtime_api.runtime_health_snapshot(_heartbeat(now - timedelta(seconds=181)), state, now=now, cfg=_cfg())
    assert out['ok'] is False
    assert out['status'] == 'RUNTIME_STALE'
