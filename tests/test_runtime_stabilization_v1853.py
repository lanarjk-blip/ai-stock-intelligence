from pathlib import Path
import ast
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


def test_sitecustomize_claims_singleton_before_worker_threads():
    src = Path("sitecustomize.py").read_text(encoding="utf-8")
    ast.parse(src)
    claim = src.index("import runtime_supervisor")
    scheduler = src.index("threading.Thread(target=_watch_worker")
    assert claim < scheduler
    assert "_IS_REAL_WORKER" in src
    assert "persistent_bridge.restore_local_from_remote()" in src
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
    assert "runtime_api_url" in bridge
    assert "type: worker" in blueprint
    assert "type: web" in blueprint
    assert "python worker_base.py" in blueprint
    assert "uvicorn runtime_api:app" in blueprint
    assert "DATABASE_URL" in blueprint and "sync: false" in blueprint
    assert "psycopg[binary]" in requirements
    assert "fastapi" in requirements and "uvicorn" in requirements
