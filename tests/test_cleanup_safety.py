import sqlite3
from datetime import datetime,timezone
import pandas as pd
import pytest
import storage_housekeeping as hk

@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    path=tmp_path/'market.db'
    monkeypatch.setattr(hk,'DB',str(path));monkeypatch.setattr(hk,'BACKUP_DIR',str(tmp_path/'backups'))
    monkeypatch.setattr(hk,'_cfg',lambda:{'housekeeping_intraday_retention_days':1,'housekeeping_engine_log_retention_days':21,'housekeeping_vacuum_enabled':False})
    monkeypatch.setattr(hk,'_market_is_live',lambda:False)
    con=sqlite3.connect(path)
    con.execute('CREATE TABLE intraday(ts TEXT,ticker TEXT)')
    con.execute('CREATE TABLE engine_log(ts TEXT,message TEXT)')
    # Representative permanent business ledgers must survive bit-for-bit.
    for table in sorted(hk.PROTECTED_TABLES-{'state'}):
        con.execute(f'CREATE TABLE {table}(evidence TEXT)')
        con.execute(f'INSERT INTO {table} VALUES(?)',('original permanent evidence',))
    con.commit();con.close()
    return path


def test_cleanup_retains_model_history_and_protected_ledgers_and_has_restorable_backup(sandbox):
    now=pd.Timestamp.now(tz='UTC')
    con=sqlite3.connect(sandbox)
    con.executemany('INSERT INTO intraday VALUES(?,?)',[(str(now-pd.Timedelta(days=d)),'TEST') for d in [61,59,45,0]])
    con.executemany('INSERT INTO engine_log VALUES(?,?)',[(str(now-pd.Timedelta(days=d)),str(d)) for d in [22,20]])
    con.commit();con.close()
    result=hk.run_cleanup(dry_run=False)
    assert result['status']=='CLEANED' and result['deleted']==2
    con=sqlite3.connect(sandbox)
    assert con.execute('SELECT COUNT(*) FROM intraday').fetchone()[0]==3
    for table in hk.PROTECTED_TABLES-{'state'}:
        assert con.execute(f'SELECT * FROM {table}').fetchall()==[('original permanent evidence',)]
    con.close()
    backup=sqlite3.connect(result['backup'])
    assert backup.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert backup.execute('SELECT COUNT(*) FROM intraday').fetchone()[0]==4
    assert backup.execute('SELECT COUNT(*) FROM engine_log').fetchone()[0]==2
    backup.close()


def test_timestamp_comparison_uses_actual_instants_not_iso_text(sandbox):
    # Same cutoff instant, different UTC offsets and separators.
    now=datetime(2026,10,27,3,0,tzinfo=timezone.utc)
    con=sqlite3.connect(sandbox)
    con.executemany('INSERT INTO engine_log VALUES(?,?)',[
        ('2026-10-06T09:59:59+07:00','old'),
        ('2026-10-06T10:00:01+07:00','new'),
        ('invalid-time','unknown')])
    con.commit();con.close()
    plan={p['table']:p for p in hk.plan_cleanup(now)}
    assert plan['engine_log']['rows']==1
    assert plan['intraday']['days']==60


def test_live_market_blocks_mutation_even_if_forced(sandbox,monkeypatch):
    con=sqlite3.connect(sandbox);con.execute("INSERT INTO engine_log VALUES('2000-01-01T00:00:00Z','old')");con.commit();con.close()
    monkeypatch.setattr(hk,'_market_is_live',lambda:True)
    r=hk.run_cleanup(dry_run=False,force=True)
    assert r['status']=='DEFERRED_MARKET' and r['deleted']==0
    con=sqlite3.connect(sandbox)
    assert con.execute('SELECT COUNT(*) FROM engine_log').fetchone()[0]==1
    con.close()


def test_backup_failure_cannot_delete_any_data(sandbox,monkeypatch):
    con=sqlite3.connect(sandbox);con.execute("INSERT INTO engine_log VALUES('2000-01-01T00:00:00Z','old')");con.commit();con.close()
    def fail():raise OSError('backup unavailable')
    monkeypatch.setattr(hk,'_backup_database',fail)
    with pytest.raises(OSError):hk.run_cleanup(dry_run=False)
    con=sqlite3.connect(sandbox)
    assert con.execute('SELECT COUNT(*) FROM engine_log').fetchone()[0]==1
    con.close()


def test_unverified_calendar_or_session_failure_blocks_maintenance(monkeypatch):
    import market_session
    for state in [dict(calendar_verified=False,status='CLOSED'),dict(calendar_verified=True,status='BREAK'),dict(calendar_verified=True,status='PRE_MARKET')]:
        monkeypatch.setattr(market_session,'session_status',lambda *a,**k:state)
        assert hk._market_is_live()
    def fail(*a,**k):raise RuntimeError('clock unavailable')
    monkeypatch.setattr(market_session,'session_status',fail)
    assert hk._market_is_live()
