import deployment_preflight as dp


class _Cursor:
    def __init__(self, row):
        self.row = row
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def execute(self, _sql):
        return None
    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=(0, 0, 0, 0, 0)):
        self.row = row
        self.closed = False
        self.rolled_back = False
    def cursor(self):
        return _Cursor(self.row)
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True


def test_preflight_ready_but_not_live_when_schema_exists_without_worker(monkeypatch):
    con = _Connection((0, 0, 0, 0, 0))
    monkeypatch.setattr(dp.persistent_bridge, "postgres_enabled", lambda: True)
    monkeypatch.setattr(dp.persistent_bridge, "ensure_remote_schema", lambda: None)
    monkeypatch.setattr(dp.persistent_bridge, "_pg_connect", lambda: con)

    result = dp.run_preflight({"DATABASE_URL": "postgresql://unit", "PORT": "8000"})
    assert result["ok"] is True
    assert result["host"]["status"] == "HOST_CONFIG_READY"
    assert result["database"]["status"] == "DATABASE_READY"
    assert result["database"]["runtime_seen"] is False
    assert con.rolled_back is True
    assert con.closed is True


def test_preflight_detects_live_worker_evidence(monkeypatch):
    monkeypatch.setattr(dp.persistent_bridge, "postgres_enabled", lambda: True)
    monkeypatch.setattr(dp.persistent_bridge, "ensure_remote_schema", lambda: None)
    monkeypatch.setattr(
        dp.persistent_bridge,
        "_pg_connect",
        lambda: _Connection((1, 4, 1, 1, 1)),
    )
    result = dp.run_preflight({"DATABASE_URL": "postgresql://unit", "PORT": "8000"})
    assert result["ok"] is True
    assert result["database"]["runtime_seen"] is True
    assert result["database"]["counts"]["heartbeat"] == 1
    assert result["database"]["counts"]["state"] == 4


def test_preflight_never_returns_exception_text_or_database_url(monkeypatch):
    secret = "postgresql://user:super-secret-password@example/db"
    monkeypatch.setattr(dp.persistent_bridge, "postgres_enabled", lambda: True)
    monkeypatch.setattr(
        dp.persistent_bridge,
        "ensure_remote_schema",
        lambda: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    result = dp.run_preflight({"DATABASE_URL": secret, "PORT": "8000"})
    rendered = str(result)
    assert secret not in rendered
    assert "super-secret-password" not in rendered
    assert result["database"]["error_type"] == "RuntimeError"
    assert result["database"]["status"] == "DATABASE_UNAVAILABLE"


def test_preflight_blocks_missing_database_url(monkeypatch):
    monkeypatch.setattr(dp.persistent_bridge, "postgres_enabled", lambda: False)
    result = dp.run_preflight({"PORT": "8000"})
    assert result["ok"] is False
    assert result["host"]["ok"] is False
    assert result["database"]["status"] == "DATABASE_URL_MISSING"
