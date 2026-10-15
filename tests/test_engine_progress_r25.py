from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import sqlite3


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def test_engine_progress_evaluator_distinguishes_running_stalled_warming_and_stale(monkeypatch):
    import local_runtime_health as h
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    cfg = {
        'engine_progress_max_age_seconds': 1200,
        'engine_result_max_age_minutes': 90,
    }

    healthy_running = {
        'engine_cycle_status': 'RUNNING',
        'engine_progress_at': iso(now - timedelta(minutes=2)),
        'engine_last_run': iso(now - timedelta(minutes=20)),
        'engine_progress_stage': 'COLLECT_PROGRESS',
    }
    out = h.engine_progress_health(healthy_running, now=now, cfg=cfg, status_prefix='LOCAL_')
    assert out['ready'] is True
    assert out['status'] == 'LOCAL_RUNTIME_READY_REFRESHING'

    stalled = dict(healthy_running, engine_progress_at=iso(now - timedelta(minutes=21)))
    out = h.engine_progress_health(stalled, now=now, cfg=cfg, status_prefix='LOCAL_')
    assert out['ready'] is False
    assert out['status'] == 'LOCAL_ENGINE_STALLED'

    warming = dict(healthy_running)
    warming.pop('engine_last_run')
    out = h.engine_progress_health(warming, now=now, cfg=cfg, status_prefix='LOCAL_')
    assert out['ready'] is False
    assert out['status'] == 'LOCAL_ENGINE_WARMING'

    stale = {
        'engine_cycle_status': 'IDLE',
        'engine_progress_at': iso(now - timedelta(minutes=95)),
        'engine_last_run': iso(now - timedelta(minutes=91)),
        'engine_progress_stage': 'CYCLE_DONE',
    }
    out = h.engine_progress_health(stale, now=now, cfg=cfg, status_prefix='LOCAL_')
    assert out['ready'] is False
    assert out['status'] == 'LOCAL_ENGINE_STALE'

    error = dict(healthy_running, engine_cycle_status='ERROR')
    out = h.engine_progress_health(error, now=now, cfg=cfg, status_prefix='LOCAL_')
    assert out['ready'] is False
    assert out['status'] == 'LOCAL_ENGINE_ERROR'


def test_local_health_blocks_fresh_heartbeat_when_engine_stalled(monkeypatch):
    import local_runtime_health as h
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    fp = 'abc123'
    monkeypatch.setattr(h, 'source_fingerprint', lambda: fp)
    monkeypatch.setattr(h, '_cfg', lambda: {
        'runtime_heartbeat_max_age_seconds': 180,
        'engine_progress_max_age_seconds': 1200,
        'engine_result_max_age_minutes': 90,
    })
    monkeypatch.setattr(h, '_state', lambda: {
        'local_worker_heartbeat_at': iso(now - timedelta(seconds=30)),
        'local_worker_source_fingerprint': fp,
        'local_worker_pid': '123',
        'engine_cycle_status': 'RUNNING',
        'engine_progress_at': iso(now - timedelta(minutes=25)),
        'engine_last_run': iso(now - timedelta(minutes=30)),
        'engine_progress_stage': 'FULL_MODEL_TRAIN',
    })
    out = h.health(now=now)
    assert out['ready'] is False
    assert out['status'] == 'LOCAL_ENGINE_STALLED'
    assert out['age_seconds'] == 30


def test_external_health_blocks_engine_stall_even_with_fresh_remote_heartbeat(monkeypatch):
    import runtime_health as rh
    now = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(rh, 'external_runtime_expected', lambda: True)
    monkeypatch.setattr(rh, '_config', lambda: {
        'runtime_heartbeat_max_age_seconds': 180,
        'engine_progress_max_age_seconds': 1200,
        'engine_result_max_age_minutes': 90,
    })
    monkeypatch.setattr(rh, '_remote_state', lambda: {
        'remote_worker_heartbeat_at': iso(now - timedelta(seconds=10)),
        'remote_worker_runtime_build': 'unit',
        'remote_worker_pid': '7',
        'engine_cycle_status': 'RUNNING',
        'engine_progress_at': iso(now - timedelta(minutes=25)),
        'engine_last_run': iso(now - timedelta(minutes=30)),
        'engine_progress_stage': 'COLLECT_PROGRESS',
    })
    out = rh.remote_runtime_health(now=now)
    assert out['ready'] is False
    assert out['status'] == 'RUNTIME_ENGINE_STALLED'
    assert out['age_seconds'] == 10


def test_checkpoint_restore_removes_stale_wal_and_shm(tmp_path, monkeypatch):
    import persistent_bridge as pb
    db = tmp_path / 'market.db'
    monkeypatch.setattr(pb, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(pb, 'LOCAL_DB', str(db))

    source = tmp_path / 'source.db'
    con = sqlite3.connect(source)
    con.execute('CREATE TABLE unit (value TEXT)')
    con.execute("INSERT INTO unit VALUES('fresh')")
    con.commit(); con.close()
    raw = source.read_bytes()
    payload = gzip.compress(raw)
    sha = hashlib.sha256(raw).hexdigest()

    db.write_bytes(b'old-database-bytes')
    (tmp_path / 'market.db-wal').write_bytes(b'old-wal')
    (tmp_path / 'market.db-shm').write_bytes(b'old-shm')

    assert pb._restore_sqlite_blob(payload, sha) is True
    assert not (tmp_path / 'market.db-wal').exists()
    assert not (tmp_path / 'market.db-shm').exists()
    con = sqlite3.connect(db)
    assert con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    assert con.execute('SELECT value FROM unit').fetchone()[0] == 'fresh'
    con.close()


def test_worker_source_has_progress_markers_inside_long_running_phases():
    src = Path('worker_base.py').read_text(encoding='utf-8')
    for token in (
        'engine_progress_at', 'engine_cycle_status', 'COLLECT_PROGRESS',
        'FULL_MODEL_TRAIN', 'WALK_FORWARD', 'FULL_ANALYSIS', 'CYCLE_DONE',
        'CYCLE_ERROR',
    ):
        assert token in src, token
    assert 'completed % 25 == 0' in src
    assert 'current_progress - last_progress >= 30' in src


def test_post_restore_heartbeat_is_reasserted_and_config_has_engine_limits():
    site = Path('sitecustomize.py').read_text(encoding='utf-8')
    first_restore = site.index('persistent_bridge.restore_worker_checkpoint_if_needed()')
    post_restore_text = 'post-restore heartbeat error'
    assert post_restore_text in site
    assert first_restore < site.index(post_restore_text)

    import json
    cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
    assert cfg['engine_progress_max_age_seconds'] == 1200
    assert cfg['engine_result_max_age_minutes'] == 90
