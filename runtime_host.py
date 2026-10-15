"""Supervised single-service Render runtime for AI Stock Intelligence.

This is an operational fallback for platforms/tooling that can create a web
service but cannot create a dedicated background worker. It runs the existing
worker and read-only runtime API as sibling subprocesses in one container.

The canonical two-service render.yaml remains supported. Do not run this host
at the same time as a separate authoritative worker against the same database.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Dict, List

HOST_VERSION = "RUNTIME-HOST-R2"
_STOP_REQUESTED = False
_CHILDREN: List[subprocess.Popen] = []


def _database_url() -> str:
    return str(
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or ""
    ).strip()


def validate_environment(env: Dict[str, str] | None = None) -> Dict[str, str]:
    source = dict(os.environ if env is None else env)
    db = str(source.get("DATABASE_URL") or source.get("NEON_DATABASE_URL") or "").strip()
    if not (db.startswith("postgres://") or db.startswith("postgresql://")):
        raise RuntimeError("DATABASE_URL/NEON_DATABASE_URL must be a PostgreSQL URL")
    port = str(source.get("PORT") or "8000").strip()
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise RuntimeError("PORT must be a valid TCP port")
    return {"database_url": db, "port": port}


def child_commands(port: str) -> Dict[str, List[str]]:
    python = sys.executable
    return {
        "worker": [python, "worker_base.py"],
        "api": [
            python,
            "-m",
            "uvicorn",
            "runtime_host_api:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
    }


def child_envs(base_env: Dict[str, str] | None = None) -> Dict[str, Dict[str, str]]:
    base = dict(os.environ if base_env is None else base_env)
    base["PYTHONUNBUFFERED"] = "1"

    worker = dict(base)
    worker["AI_ENGINE_WORKER"] = "1"
    worker["ASI_EXTERNAL_RUNTIME"] = "1"

    api = dict(base)
    api.pop("AI_ENGINE_WORKER", None)
    api["ASI_EXTERNAL_RUNTIME"] = "1"
    return {"worker": worker, "api": api}


def _terminate_process(proc: subprocess.Popen, grace_seconds: float = 8.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.terminate()
        except Exception:
            return

    deadline = time.monotonic() + max(0.5, float(grace_seconds))
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)

    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass


def _shutdown_children() -> None:
    for proc in list(reversed(_CHILDREN)):
        _terminate_process(proc)


def _signal_handler(signum, _frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    print(f"[RUNTIME_HOST] signal={signum} shutting down", flush=True)
    _shutdown_children()


def run() -> int:
    try:
        cfg = validate_environment()
    except Exception as exc:
        print(f"[RUNTIME_HOST] CONFIG_ERROR {exc}", flush=True)
        return 78

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    commands = child_commands(cfg["port"])
    envs = child_envs()
    print(
        f"[RUNTIME_HOST] START version={HOST_VERSION} port={cfg['port']} "
        "mode=worker+api",
        flush=True,
    )

    try:
        for name in ("worker", "api"):
            proc = subprocess.Popen(
                commands[name],
                env=envs[name],
                stdout=None,
                stderr=None,
                start_new_session=True,
            )
            _CHILDREN.append(proc)
            print(f"[RUNTIME_HOST] child={name} pid={proc.pid} started", flush=True)

        names = {id(proc): name for name, proc in zip(("worker", "api"), _CHILDREN)}
        while not _STOP_REQUESTED:
            for proc in list(_CHILDREN):
                code = proc.poll()
                if code is not None:
                    name = names.get(id(proc), "child")
                    print(
                        f"[RUNTIME_HOST] child={name} pid={proc.pid} exited code={code}; "
                        "terminating sibling runtime",
                        flush=True,
                    )
                    _shutdown_children()
                    return 1
            time.sleep(0.5)
        return 0
    finally:
        _shutdown_children()


if __name__ == "__main__":
    raise SystemExit(run())
