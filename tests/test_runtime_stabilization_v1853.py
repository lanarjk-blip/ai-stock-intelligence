from pathlib import Path
import ast
import os
import sqlite3


def test_singleton_worker_contract_is_explicit():
    src = Path("runtime_supervisor.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "LOCK_EX" in src and "LOCK_NB" in src
    assert "worker_base.py" in src
    assert "DUPLICATE_EXIT" in src
    assert "SIGTERM" in src
    assert "os._exit(0)" in src
    assert "RUNTIME_BUILD" in src
    assert "RENDER_GIT_COMMIT" in src
    assert "deploy_sha" in src


def test_sitecustomize_claims_singleton_and_restores_checkpoint_first():
    src = Path("sitecustomize.py").read_text(encoding="utf-8")
    ast.parse(src)
    claim = src.index("import runtime_supervisor")
    checkpoint = src.index("persistent_bridge.restore_worker_checkpoint_if_needed()")
    control = src.index("persistent_bridge.restore_local_from_postgres()")
    scheduler = src.index("threading.Thread(target=_watch_worker")
    assert claim < checkpoint < control < scheduler
    assert "_IS_REAL_WORKER" in src
    assert "_IS_RUNTIME_API" in src
    assert "persistent_bridge.start_worker_bridge" in src
    assert "persistent_bridge.api_enabled()" in src
    assert "external_runtime_guard.install()" in src
    assert '"persistent_bridge.py"' in src
    assert '"runtime_supervisor.py"' in src
    assert '"external_runtime_guard.py"' in src


def test_external_runtime_guard_only_blocks_worker_base():
    src = Path("external_runtime_guard.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "def _is_worker_launch" in src
    assert 'os.path.basename(part) == "worker_base.py"' in src
    assert "subprocess.Popen = guarded_popen" in src
    assert "return _ORIGINAL_POPEN" in src
    assert "_ExternalWorkerHandle" in src


def test_premarket_has_lightweight_on_open_self_heal():
    preliminary = Path("premarket_preliminary.py").read_text(encoding="utf-8")
    ui = Path("premarket_ui_patch.py").read_text(encoding="utf-8")
    ast.parse(preliminary)
    ast.parse(ui)
    assert "def ensure_on_open" in preliminary
    assert "allow_full_analysis=False" in preliminary
    assert '"NO_SNAPSHOT"' in preliminary
    assert "PRELIMINARY_ON_OPEN" in preliminary
    assert "premarket_preliminary.ensure_on_open()" in ui
    assert 'str(body).strip() == "🌅 Pre-Market Top Picks"' in ui
    assert 'status == "PRELIMINARY"' in ui
    assert "st.rerun()" in ui
    assert "worker.singleton.lock" in ui
    assert "remote_worker_runtime_build" in ui


def test_persistent_bridge_local_restore_roundtrip(tmp_path, monkeypatch):
    import persistent_bridge as bridge

    db = tmp_path / "market.db"
    monkeypatch.setattr(bridge, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(bridge, "LOCAL_DB", str(db))

    counts = bridge._write_local_payload(
        {"data_quality_status": "READY", "snapshot_source": "unit"},
        [
            {"rank": 1, "ticker": "BBCA", "decision": "WAIT", "ai_score": 88.0},
            {"rank": 2, "ticker": "BMRI", "decision": "MONITOR", "ai_score": 80.0},
        ],
        [
            {
                "market_date": "2026-10-15",
                "generated_at": "2026-10-14T22:00:00+00:00",
                "status": "PRELIMINARY",
                "pick_count": 1,
                "note": "test",
            }
        ],
        [
            {
                "market_date": "2026-10-15",
                "generated_at": "2026-10-14T22:00:00+00:00",
                "rank": 1,
                "ticker": "BBCA",
                "company": "Bank Central Asia",
                "decision": "WAIT",
                "confidence": 70.0,
                "ai_score": 88.0,
                "risk": "MEDIUM",
                "entry_low": 100.0,
                "entry_high": 101.0,
                "stop_loss": 95.0,
                "target_1": 105.0,
                "target_2": 110.0,
                "ihsg_regime": "SIDEWAYS",
                "reason": "test",
                "source_phase": "unit",
            }
        ],
    )

    assert counts == {"state": 2, "opportunities": 2, "runs": 1, "picks": 1}
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT value FROM state WHERE key='data_quality_status'").fetchone()[0] == "READY"
        assert con.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 2
        assert con.execute("SELECT status FROM premarket_runs").fetchone()[0] == "PRELIMINARY"
        assert con.execute("SELECT ticker FROM premarket_picks").fetchone()[0] == "BBCA"
    finally:
        con.close()


def test_full_sqlite_and_model_checkpoint_roundtrip(tmp_path, monkeypatch):
    import persistent_bridge as bridge

    db = tmp_path / "market.db"
    monkeypatch.setattr(bridge, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(bridge, "LOCAL_DB", str(db))
    monkeypatch.setattr(bridge, "WARM_PRICE_ROWS", 1)
    monkeypatch.setattr(bridge, "MAX_ARTIFACT_BYTES", 1024 * 1024)

    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE prices (ticker TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)"
    )
    con.executemany(
        "INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
        [
            ("BBCA", "2026-10-13", 100, 105, 99, 103, 1000),
            ("BMRI", "2026-10-13", 90, 94, 89, 93, 2000),
        ],
    )
    con.execute("CREATE TABLE state (key TEXT PRIMARY KEY,value TEXT)")
    con.execute("INSERT INTO state VALUES('engine_version','unit')")
    con.commit()
    con.close()

    model = tmp_path / "jst_model_v188.npz"
    model.write_bytes(b"unit-model-bytes")

    sqlite_gzip, sqlite_sha, raw_bytes = bridge._consistent_sqlite_bytes()
    artifacts_gzip, artifacts_sha = bridge._artifact_archive_bytes()
    assert sqlite_gzip and sqlite_sha and raw_bytes > 0
    assert artifacts_gzip and artifacts_sha

    db.unlink()
    model.unlink()
    assert bridge._restore_sqlite_blob(sqlite_gzip, sqlite_sha) is True
    restored_artifacts = bridge._restore_artifacts_blob(artifacts_gzip, artifacts_sha)
    assert restored_artifacts == 1
    assert model.read_bytes() == b"unit-model-bytes"

    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
        assert con.execute("SELECT value FROM state WHERE key='engine_version'").fetchone()[0] == "unit"
    finally:
        con.close()


def test_api_bootstrap_restore_is_functional(tmp_path, monkeypatch):
    import persistent_bridge as bridge
    import requests

    db = tmp_path / "market.db"
    monkeypatch.setattr(bridge, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(bridge, "LOCAL_DB", str(db))
    monkeypatch.setattr(bridge, "runtime_api_url", lambda: "https://runtime.example")

    payload = {
        "state": {"data_quality_status": "READY"},
        "opportunity_snapshot": {
            "payload": [{"rank": 1, "ticker": "BBCA", "decision": "WAIT", "ai_score": 90.0}]
        },
        "premarket_runs": [
            {
                "market_date": "2026-10-15",
                "generated_at": "2026-10-14T23:00:00+00:00",
                "status": "PRELIMINARY",
                "pick_count": 1,
                "note": "unit",
            }
        ],
        "premarket_picks": [
            {
                "market_date": "2026-10-15",
                "generated_at": "2026-10-14T23:00:00+00:00",
                "rank": 1,
                "ticker": "BBCA",
                "company": "Bank Central Asia",
                "decision": "WAIT",
                "confidence": 71.0,
                "ai_score": 90.0,
                "risk": "MEDIUM",
                "entry_low": 100.0,
                "entry_high": 101.0,
                "stop_loss": 95.0,
                "target_1": 105.0,
                "target_2": 110.0,
                "ihsg_regime": "SIDEWAYS",
                "reason": "unit",
                "source_phase": "api-unit",
            }
        ],
        "heartbeat": {
            "worker_pid": 321,
            "worker_generation": "gen-unit",
            "runtime_build": "R21+abcdef123456",
            "heartbeat_at": "2026-10-14T23:00:00+00:00",
        },
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_get(url, *args, **kwargs):
        assert url == "https://runtime.example/bootstrap"
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    result = bridge.restore_local_from_api()
    assert result["status"] == "RESTORED_API"
    assert result["opportunities"] == 1
    assert result["runs"] == 1
    assert result["picks"] == 1

    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT ticker FROM opportunities").fetchone()[0] == "BBCA"
        assert con.execute("SELECT status FROM premarket_runs").fetchone()[0] == "PRELIMINARY"
        state = dict(con.execute(
            "SELECT key,value FROM state WHERE key LIKE 'remote_worker_%'"
        ).fetchall())
        assert state["remote_worker_runtime_build"] == "R21+abcdef123456"
        assert state["remote_worker_generation"] == "gen-unit"
        assert state["remote_worker_pid"] == "321"
    finally:
        con.close()


def test_runtime_api_filters_sensitive_state_keys():
    import runtime_api

    assert runtime_api._safe_state_key("data_quality_status") is True
    assert runtime_api._safe_state_key("snapshot_source") is True
    for key in (
        "api_token", "client_secret", "db_password", "service_account_json",
        "private_key", "api_key", "whatsapp_access", "fcm_device_token",
    ):
        assert runtime_api._safe_state_key(key) is False, key


def test_runtime_api_and_render_blueprint_contract():
    api = Path("runtime_api.py").read_text(encoding="utf-8")
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    bridge = Path("persistent_bridge.py").read_text(encoding="utf-8")
    ast.parse(api)
    ast.parse(bridge)

    assert '@app.get("/health")' in api
    assert '@app.get("/bootstrap")' in api
    assert "_SENSITIVE_STATE_PARTS" in api
    assert "if _safe_state_key(k)" in api
    assert "def restore_local_from_api" in bridge
    assert "def sync_worker_checkpoint" in bridge
    assert "def restore_worker_checkpoint_if_needed" in bridge
    assert "sqlite3.connect" in bridge and ".backup(target)" in bridge
    assert "PRAGMA quick_check" in bridge
    assert "asi_worker_checkpoint" in bridge
    assert "runtime_api_url" in bridge
    assert "type: worker" in blueprint
    assert "type: web" in blueprint
    assert "python worker_base.py" in blueprint
    assert "uvicorn runtime_api:app" in blueprint
    assert "ASI_CHECKPOINT_SECONDS" in blueprint
    assert "DATABASE_URL" in blueprint and "sync: false" in blueprint
    assert "psycopg[binary]" in requirements
    assert "fastapi" in requirements and "uvicorn" in requirements
