from pathlib import Path

path = Path('ui_replica.py')
src = path.read_text(encoding='utf-8')

old_sync = '''    with con.cursor() as cur:\n        for table, payload in replica["tables"].items():\n'''
new_sync = '''    with con.cursor() as cur:\n        present_tables = set(replica["tables"])\n        for table in TABLE_SPECS:\n            if table not in present_tables:\n                cur.execute(\n                    "DELETE FROM asi_ui_table_snapshots WHERE table_name=%s",\n                    (table,),\n                )\n        present_files = set(replica["files"])\n        for name in SAFE_JSON_FILES:\n            if name not in present_files:\n                cur.execute(\n                    "DELETE FROM asi_ui_files WHERE file_name=%s",\n                    (name,),\n                )\n\n        for table, payload in replica["tables"].items():\n'''
if old_sync not in src:
    raise SystemExit('sync anchor missing')
src = src.replace(old_sync, new_sync, 1)

old_write = '''    try:\n        for table, payload in tables.items():\n            if table not in TABLE_SPECS:\n                continue\n            try:\n                payload = payload or {}\n                columns = [str(c) for c in (payload.get("columns") or [])]\n                rows = payload.get("rows") or []\n                if not columns and rows:\n                    columns = list(rows[0].keys())\n                if not columns:\n                    continue\n                frame = pd.DataFrame(rows, columns=columns)\n                frame.to_sql(table, con, if_exists="replace", index=False)\n                written_tables += 1\n            except Exception as exc:\n                table_errors[table] = f"{type(exc).__name__}: {exc}"[:300]\n        con.commit()\n'''
new_write = '''    try:\n        incoming_tables = {str(name) for name in tables if str(name) in TABLE_SPECS}\n        for table in TABLE_SPECS:\n            if table in incoming_tables:\n                continue\n            try:\n                if _table_exists(con, table):\n                    con.execute(f'DELETE FROM "{table}"')\n            except Exception as exc:\n                table_errors[table] = f"{type(exc).__name__}: {exc}"[:300]\n\n        for table, payload in tables.items():\n            if table not in TABLE_SPECS:\n                continue\n            try:\n                payload = payload or {}\n                columns = [str(c) for c in (payload.get("columns") or [])]\n                rows = payload.get("rows") or []\n                if not columns and rows:\n                    columns = list(rows[0].keys())\n                if not columns:\n                    if _table_exists(con, table):\n                        con.execute(f'DELETE FROM "{table}"')\n                    written_tables += 1\n                    continue\n                frame = pd.DataFrame(rows, columns=columns)\n                frame.to_sql(table, con, if_exists="replace", index=False)\n                written_tables += 1\n            except Exception as exc:\n                table_errors[table] = f"{type(exc).__name__}: {exc}"[:300]\n        con.commit()\n'''
if old_write not in src:
    raise SystemExit('write anchor missing')
src = src.replace(old_write, new_write, 1)

old_files = '''    written_files = 0\n    os.makedirs(db_dir, exist_ok=True)\n    for name, payload in files.items():\n'''
new_files = '''    written_files = 0\n    os.makedirs(db_dir, exist_ok=True)\n    incoming_files = {str(name) for name in files if str(name) in SAFE_JSON_FILES}\n    for name in SAFE_JSON_FILES:\n        if name in incoming_files:\n            continue\n        target = os.path.join(db_dir, name)\n        try:\n            if os.path.exists(target):\n                os.remove(target)\n        except OSError:\n            pass\n\n    for name, payload in files.items():\n'''
if old_files not in src:
    raise SystemExit('file anchor missing')
src = src.replace(old_files, new_files, 1)

path.write_text(src, encoding='utf-8')

test_path = Path('tests/test_ui_replica_v1853.py')
tests = test_path.read_text(encoding='utf-8')
marker = 'def test_ui_replica_exact_mirror_clears_stale_local_state'
if marker not in tests:
    tests += r'''


def test_ui_replica_exact_mirror_clears_stale_local_state(tmp_path):
    import ui_replica

    target = tmp_path / "market.db"
    con = sqlite3.connect(target)
    con.execute("CREATE TABLE dynamic_opportunities (ticker TEXT, rank INTEGER)")
    con.execute("INSERT INTO dynamic_opportunities VALUES('OLD',1)")
    con.execute("CREATE TABLE hold_intelligence (ticker TEXT, hold_score REAL)")
    con.execute("INSERT INTO hold_intelligence VALUES('STALE',99)")
    con.commit()
    con.close()

    db_dir = tmp_path / "dbfiles"
    db_dir.mkdir()
    stale_file = db_dir / "model_validation.json"
    stale_file.write_text('{"stale": true}', encoding="utf-8")

    replica = {
        "tables": {
            "dynamic_opportunities": {
                "columns": ["ticker", "rank"],
                "rows": [],
            }
        },
        "files": {},
    }
    result = ui_replica.write_local(replica, str(target), str(db_dir))
    assert result["errors"] == {}

    con = sqlite3.connect(target)
    try:
        assert con.execute("SELECT COUNT(*) FROM dynamic_opportunities").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM hold_intelligence").fetchone()[0] == 0
    finally:
        con.close()
    assert not stale_file.exists()


def test_ui_replica_remote_sync_deletes_missing_snapshots(tmp_path, monkeypatch):
    import ui_replica

    source = tmp_path / "source.db"
    con = sqlite3.connect(source)
    con.execute("CREATE TABLE dynamic_opportunities (ticker TEXT, rank INTEGER)")
    con.execute("INSERT INTO dynamic_opportunities VALUES('BBCA',1)")
    con.commit()
    con.close()

    class FakeCursor:
        def __init__(self):
            self.calls = []
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, sql, params=None):
            self.calls.append((" ".join(str(sql).split()), params))
            return self

    class FakeConnection:
        def __init__(self):
            self.cur = FakeCursor()
        def cursor(self):
            return self.cur

    fake = FakeConnection()
    monkeypatch.setattr(ui_replica, "ensure_remote_schema", lambda con: None)
    result = ui_replica.sync_to_remote(fake, str(source), str(tmp_path), "R26")
    assert result["errors"] == {}

    table_deletes = [params for sql, params in fake.cur.calls if sql.startswith("DELETE FROM asi_ui_table_snapshots")]
    file_deletes = [params for sql, params in fake.cur.calls if sql.startswith("DELETE FROM asi_ui_files")]
    assert ("hold_intelligence",) in table_deletes
    assert ("dynamic_opportunities",) not in table_deletes
    assert ("model_validation.json",) in file_deletes
'''
    test_path.write_text(tests, encoding='utf-8')

print('R26 UI REPLICA EXACT-MIRROR PATCH APPLIED')
