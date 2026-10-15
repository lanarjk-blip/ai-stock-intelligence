import os

import pytest

import hybrid_worker_lease as hl


def test_device_detection_and_priority():
    assert hl.detect_device_type({"PREFIX": "/data/data/com.termux/files/usr"}) == "hp"
    assert hl.detect_device_type({"ASI_WORKER_DEVICE": "laptop"}) == "laptop"
    assert hl.detect_device_type({"RENDER": "true"}) == "render"
    assert hl.device_priority("render") > hl.device_priority("laptop") > hl.device_priority("hp")


def test_startup_bias_prefers_render_then_laptop_then_hp(monkeypatch):
    monkeypatch.delenv("ASI_HYBRID_CLAIM_DELAY_SECONDS", raising=False)
    assert hl.startup_delay_seconds("render") == 0
    assert hl.startup_delay_seconds("laptop") < hl.startup_delay_seconds("hp")


def test_missing_postgres_url_fails_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        hl._pg_connect()


def test_identity_contains_device_generation_and_no_database_secret(monkeypatch):
    monkeypatch.setenv("ASI_WORKER_DEVICE", "hp")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@example/db")
    ident = hl._identity({"generation": "abc123", "build": "r39", "pid": 7})
    rendered = str(ident)
    assert ident["device_type"] == "hp"
    assert ident["generation"] == "abc123"
    assert "secret" not in rendered
    assert "postgresql://" not in rendered


def test_lease_sql_is_expiry_or_same_holder_only():
    source = open(hl.__file__, "r", encoding="utf-8").read()
    assert "asi_worker_lease.lease_until <= now()" in source
    assert "asi_worker_lease.holder_id=excluded.holder_id" in source
    assert "LEASE_LOST fail-closed" in source
    assert "RENEW_EXPIRED fail-closed" in source


def test_hybrid_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASI_HYBRID_WORKER", raising=False)
    assert hl.enabled() is False
    monkeypatch.setenv("ASI_HYBRID_WORKER", "1")
    assert hl.enabled() is True
