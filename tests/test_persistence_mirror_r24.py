from pathlib import Path
import sqlite3

import persistent_bridge as pb


def _run(date, count, note='PRELIMINARY', status='PRELIMINARY'):
    return {
        'market_date': date,
        'generated_at': '2026-10-15T00:00:00+00:00',
        'status': status,
        'pick_count': count,
        'note': note,
    }


def _pick(date, rank, ticker=None):
    return {
        'market_date': date,
        'generated_at': '2026-10-15T00:00:00+00:00',
        'rank': rank,
        'ticker': ticker or f'T{rank}',
        'company': ticker or f'T{rank}',
        'decision': 'WAIT',
        'confidence': 50.0,
        'ai_score': 60.0,
        'risk': 'MEDIUM',
        'reference_price': 100.0,
        'entry_low': 98.0,
        'entry_high': 102.0,
        'stop_loss': 95.0,
        'target_1': 105.0,
        'target_2': 108.0,
        'ihsg_regime': 'SIDEWAYS',
        'reason': 'test',
        'source_phase': 'test',
    }


def test_local_restore_is_exact_mirror_and_removes_ghost_ranks(tmp_path, monkeypatch):
    db = tmp_path / 'market.db'
    monkeypatch.setattr(pb, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(pb, 'LOCAL_DB', str(db))
    date = '2026-10-16'

    pb._write_local_payload(
        {},
        [{'ticker': 'AAA', 'rank': 1, 'updated_at': '2026-10-15T00:00:00+00:00'}],
        [_run(date, 5)],
        [_pick(date, i) for i in range(1, 6)],
    )
    con = sqlite3.connect(db)
    assert con.execute('SELECT COUNT(*) FROM premarket_picks WHERE market_date=?', (date,)).fetchone()[0] == 5
    assert con.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0] == 1
    con.close()

    result = pb._write_local_payload(
        {}, [], [_run(date, 3)], [_pick(date, i) for i in range(1, 6)]
    )
    con = sqlite3.connect(db)
    ranks = [r[0] for r in con.execute(
        'SELECT rank FROM premarket_picks WHERE market_date=? ORDER BY rank', (date,)
    ).fetchall()]
    assert ranks == [1, 2, 3]
    assert con.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0] == 0
    con.close()
    assert result['picks'] == 3


def test_local_restore_never_downgrades_h10_final(tmp_path, monkeypatch):
    db = tmp_path / 'market.db'
    monkeypatch.setattr(pb, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(pb, 'LOCAL_DB', str(db))
    date = '2026-10-16'
    final_note = 'H10_FINAL H10-R1 mode=MONITOR_FALLBACK'

    pb._write_local_payload(
        {}, [], [_run(date, 2, final_note, 'LOCKED')],
        [_pick(date, 1, 'FINAL1'), _pick(date, 2, 'FINAL2')],
    )
    result = pb._write_local_payload(
        {}, [], [_run(date, 1, 'PRELIMINARY stale worker')],
        [_pick(date, 1, 'STALE1')],
    )
    con = sqlite3.connect(db)
    note, status, count = con.execute(
        'SELECT note,status,pick_count FROM premarket_runs WHERE market_date=?', (date,)
    ).fetchone()
    tickers = [r[0] for r in con.execute(
        'SELECT ticker FROM premarket_picks WHERE market_date=? ORDER BY rank', (date,)
    ).fetchall()]
    con.close()
    assert note == final_note
    assert status == 'LOCKED'
    assert count == 2
    assert tickers == ['FINAL1', 'FINAL2']
    assert result['protected_final_dates'] == 1


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        compact = ' '.join(str(sql).split())
        self.calls.append((compact, params))
        if compact.startswith('SELECT market_date,note FROM asi_premarket_runs'):
            self._rows = [('2026-10-16', 'H10_FINAL H10-R1')]
        else:
            self._rows = []
        return self

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_remote_sync_replaces_pick_set_and_protects_existing_final(monkeypatch):
    fake = _FakeConnection()
    local = {
        'state': [],
        'opportunities': [],
        'runs': [
            _run('2026-10-16', 1, 'PRELIMINARY stale worker'),
            _run('2026-10-17', 2, 'H10_FINAL H10-R1', 'LOCKED'),
            _run('2026-10-20', 0, 'NO_TRADE', 'NO_TRADE'),
        ],
        'picks': [
            _pick('2026-10-16', 1, 'STALE'),
            _pick('2026-10-17', 1, 'F1'),
            _pick('2026-10-17', 2, 'F2'),
            _pick('2026-10-17', 3, 'GHOST'),
        ],
    }
    monkeypatch.setattr(pb, 'postgres_enabled', lambda: True)
    monkeypatch.setattr(pb, 'ensure_remote_schema', lambda: True)
    monkeypatch.setattr(pb, '_read_local_payload', lambda: local)
    monkeypatch.setattr(pb, '_pg_connect', lambda: fake)

    result = pb.sync_local_to_remote()
    calls = fake.cur.calls
    deletes = [p for sql, p in calls if sql.startswith('DELETE FROM asi_premarket_picks')]
    inserts = [p for sql, p in calls if sql.startswith('INSERT INTO asi_premarket_picks')]
    assert ('2026-10-16',) not in deletes
    assert ('2026-10-17',) in deletes
    assert ('2026-10-20',) in deletes
    assert [p[1] for p in inserts if p[0] == '2026-10-17'] == [1, 2]
    assert all(p[0] != '2026-10-16' for p in inserts)
    assert any(sql.startswith('DELETE FROM asi_opportunity_snapshot') for sql, _ in calls)
    assert result['protected_final_dates'] == 1
    assert result['picks'] == 2
    assert fake.committed is True


def test_runtime_api_bootstrap_filters_pick_rows_by_authoritative_pick_count():
    text = Path('runtime_api.py').read_text(encoding='utf-8')
    compact = ' '.join(text.split())
    assert 'WHERE p.rank BETWEEN 1 AND r.pick_count' in compact
    bridge = Path('persistent_bridge.py').read_text(encoding='utf-8')
    assert 'PERSIST-BRIDGE-R4-EXACT-MIRROR' in bridge
    assert 'snapshot_generated_at' in bridge
