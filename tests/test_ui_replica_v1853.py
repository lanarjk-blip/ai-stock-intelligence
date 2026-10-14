import json
import sqlite3


def test_ui_replica_whitelist_and_log_redaction(tmp_path):
    import ui_replica

    source = tmp_path / "source.db"
    con = sqlite3.connect(source)
    con.execute("""CREATE TABLE dynamic_opportunities (
        ticker TEXT, rank INTEGER, opportunity_score REAL, state TEXT
    )""")
    con.execute("INSERT INTO dynamic_opportunities VALUES('BBCA',1,88.5,'MONITOR')")
    con.execute("""CREATE TABLE recommendation_history (
        id INTEGER, generated_at TEXT, ticker TEXT, recommendation TEXT
    )""")
    con.execute("INSERT INTO recommendation_history VALUES(1,'2026-10-15T00:00:00Z','BBCA','WAIT')")
    con.execute("""CREATE TABLE engine_log (
        id INTEGER, ts TEXT, phase TEXT, status TEXT, detail TEXT
    )""")
    con.execute(
        "INSERT INTO engine_log VALUES(1,'2026-10-15T00:00:00Z','TEST','ERROR',?)",
        ("authorization=BearerSecret token=abc123 password=hunter2 harmless=yes",),
    )
    con.execute("CREATE TABLE prices (ticker TEXT,date TEXT,close REAL)")
    con.execute("INSERT INTO prices VALUES('BBCA','2026-10-15',100)")
    con.commit()
    con.close()

    replica = ui_replica.read_local(str(source), str(tmp_path))
    assert "dynamic_opportunities" in replica["tables"]
    assert "recommendation_history" in replica["tables"]
    assert "engine_log" in replica["tables"]
    assert "prices" not in replica["tables"]
    detail = replica["tables"]["engine_log"]["rows"][0]["detail"]
    assert "BearerSecret" not in detail
    assert "abc123" not in detail
    assert "hunter2" not in detail
    assert "[REDACTED]" in detail

    target = tmp_path / "target.db"
    model_validation = tmp_path / "model_validation.json"
    model_validation.write_text(json.dumps({"summary": [{"metric": "accuracy", "value": 0.6}]}), encoding="utf-8")
    replica = ui_replica.read_local(str(source), str(tmp_path))
    result = ui_replica.write_local(replica, str(target), str(tmp_path / "target_files"))
    assert result["tables"] >= 3
    assert result["files"] == 1

    con = sqlite3.connect(target)
    try:
        assert con.execute("SELECT ticker FROM dynamic_opportunities").fetchone()[0] == "BBCA"
        assert con.execute("SELECT ticker FROM recommendation_history").fetchone()[0] == "BBCA"
    finally:
        con.close()


def test_runtime_pull_restores_core_and_ui_replica(tmp_path, monkeypatch):
    import persistent_bridge
    import replica_runtime
    import requests

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    local_db = db_dir / "market.db"
    monkeypatch.setattr(persistent_bridge, "DB_DIR", str(db_dir))
    monkeypatch.setattr(persistent_bridge, "LOCAL_DB", str(local_db))
    monkeypatch.setattr(persistent_bridge, "runtime_api_url", lambda: "https://runtime.example")

    payload = {
        "state": {"data_quality_status": "READY", "snapshot_source": "worker"},
        "opportunity_snapshot": {
            "payload": [
                {"rank": 1, "ticker": "BBCA", "decision": "WAIT", "ai_score": 88.0}
            ]
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
                "confidence": 70.0,
                "ai_score": 88.0,
                "risk": "MEDIUM",
                "entry_low": 100.0,
                "entry_high": 101.0,
                "stop_loss": 95.0,
                "target_1": 105.0,
                "target_2": 110.0,
                "ihsg_regime": "SIDEWAYS",
                "reason": "unit",
                "source_phase": "unit",
            }
        ],
        "ui_replica": {
            "tables": {
                "dynamic_opportunities": {
                    "columns": ["ticker", "rank", "opportunity_score", "state"],
                    "rows": [
                        {"ticker": "BBCA", "rank": 1, "opportunity_score": 90.0, "state": "MONITOR"}
                    ],
                },
                "hold_intelligence": {
                    "columns": ["ticker", "hold_score", "state"],
                    "rows": [
                        {"ticker": "BBCA", "hold_score": 72.0, "state": "HOLD CANDIDATE"}
                    ],
                },
            },
            "files": {
                "model_validation.json": {"summary": [{"metric": "accuracy", "value": 0.61}]}
            },
        },
        "heartbeat": {
            "worker_pid": 777,
            "worker_generation": "generation-unit",
            "runtime_build": "V1.8.52-R21+abcdef123456",
            "heartbeat_at": "2026-10-15T00:00:00+00:00",
            "bridge_version": "unit",
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
    result = replica_runtime.pull_ui_once()
    assert result["status"] == "PULLED"
    assert result["ui"]["tables"] == 2
    assert result["ui"]["files"] == 1

    con = sqlite3.connect(local_db)
    try:
        assert con.execute("SELECT ticker FROM opportunities").fetchone()[0] == "BBCA"
        assert con.execute("SELECT status FROM premarket_runs").fetchone()[0] == "PRELIMINARY"
        assert con.execute("SELECT ticker FROM dynamic_opportunities").fetchone()[0] == "BBCA"
        assert con.execute("SELECT state FROM hold_intelligence").fetchone()[0] == "HOLD CANDIDATE"
        state = dict(con.execute("SELECT key,value FROM state WHERE key LIKE 'remote_worker_%'").fetchall())
        assert state["remote_worker_pid"] == "777"
        assert state["remote_worker_generation"] == "generation-unit"
    finally:
        con.close()

    validation = json.loads((db_dir / "model_validation.json").read_text(encoding="utf-8"))
    assert validation["summary"][0]["value"] == 0.61


def test_sitecustomize_wires_continuous_ui_replication():
    from pathlib import Path

    src = Path("sitecustomize.py").read_text(encoding="utf-8")
    assert "replica_runtime.start_worker_replica()" in src
    assert "replica_runtime.pull_ui_once()" in src
    assert "replica_runtime.start_ui_pull()" in src
    assert '"ui_replica.py"' in src
    assert '"replica_runtime.py"' in src
