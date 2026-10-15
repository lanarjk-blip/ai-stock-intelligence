import pytest

import runtime_host


def test_validate_environment_requires_postgres_url():
    with pytest.raises(RuntimeError):
        runtime_host.validate_environment({"PORT": "8000"})
    with pytest.raises(RuntimeError):
        runtime_host.validate_environment({"DATABASE_URL": "sqlite:///tmp/x.db", "PORT": "8000"})


def test_validate_environment_accepts_neon_style_postgres_url():
    out = runtime_host.validate_environment(
        {"DATABASE_URL": "postgresql://user:pass@example.test/db", "PORT": "10000"}
    )
    assert out["port"] == "10000"
    assert out["database_url"].startswith("postgresql://")


def test_child_envs_make_worker_authoritative_but_not_api():
    envs = runtime_host.child_envs(
        {"DATABASE_URL": "postgresql://u:p@host/db", "AI_ENGINE_WORKER": "stale"}
    )
    assert envs["worker"]["AI_ENGINE_WORKER"] == "1"
    assert envs["worker"]["ASI_EXTERNAL_RUNTIME"] == "1"
    assert "AI_ENGINE_WORKER" not in envs["api"]
    assert envs["api"]["ASI_EXTERNAL_RUNTIME"] == "1"


def test_child_commands_bind_api_to_render_port_and_use_existing_worker():
    commands = runtime_host.child_commands("12345")
    assert commands["worker"][-1] == "worker_base.py"
    assert "runtime_host_api:app" in commands["api"]
    assert commands["api"][-1] == "12345"


def test_invalid_port_is_rejected():
    with pytest.raises(RuntimeError):
        runtime_host.validate_environment(
            {"DATABASE_URL": "postgresql://u:p@host/db", "PORT": "99999"}
        )
