"""Single-worker runtime supervisor for AI Stock Intelligence.

Streamlit can restart while a detached worker process survives. A new UI server
can then spawn another worker, leaving two generations writing contradictory
state to the same database. This module makes the worker process itself own the
singleton lock and retires stale sibling workers.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

_BASE_BUILD = "V1.8.52-SINGLETON"
_DEPLOY_SHA = str(
    os.environ.get("RENDER_GIT_COMMIT")
    or os.environ.get("GITHUB_SHA")
    or ""
).strip()[:12]
RUNTIME_BUILD = f"{_BASE_BUILD}+{_DEPLOY_SHA}" if _DEPLOY_SHA else _BASE_BUILD
SUPERVISOR_VERSION = "RUNTIME-SUPERVISOR-R1"
LOCK_PATH = "/tmp/ai_stock_intelligence_worker.singleton.lock"
LOCK_HANDLE = None
GENERATION = uuid.uuid4().hex[:12]
STARTED_AT = datetime.now(timezone.utc).isoformat()
try:
    from local_runtime_health import source_fingerprint as _source_fingerprint
    SOURCE_FINGERPRINT = _source_fingerprint()
except Exception:
    SOURCE_FINGERPRINT = "unknown"


def is_real_worker() -> bool:
    return (
        os.environ.get("AI_ENGINE_WORKER") == "1"
        and os.path.basename(str(sys.argv[0] or "")) == "worker_base.py"
    )


def _worker_pids():
    if os.name != "posix" or not os.path.isdir("/proc"):
        return []
    current = os.getpid()
    uid = os.getuid() if hasattr(os, "getuid") else None
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == current:
            continue
        proc_dir = os.path.join("/proc", name)
        try:
            if uid is not None and os.stat(proc_dir).st_uid != uid:
                continue
            with open(os.path.join(proc_dir, "cmdline"), "rb") as fh:
                cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            if "worker_base.py" in cmd:
                found.append(pid)
        except Exception:
            continue
    return found


def _read_lock_identity(handle):
    try:
        handle.seek(0)
        raw = handle.read().strip()
    except Exception:
        raw = ""
    out = {}
    for token in raw.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    try:
        out["pid"] = int(out.get("pid") or 0)
    except Exception:
        out["pid"] = 0
    return out


def _pid_is_verified_worker(pid):
    try:
        pid = int(pid or 0)
        if pid <= 0 or pid == os.getpid() or os.name != "posix":
            return False
        proc_dir = f"/proc/{pid}"
        if not os.path.isdir(proc_dir):
            return False
        if hasattr(os, "getuid") and os.stat(proc_dir).st_uid != os.getuid():
            return False
        with open(os.path.join(proc_dir, "cmdline"), "rb") as fh:
            cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        return "worker_base.py" in cmd
    except Exception:
        return False


def _should_takeover(identity):
    """Only replace the verified lock owner when its startup fingerprint is stale."""
    pid = int((identity or {}).get("pid") or 0)
    if not _pid_is_verified_worker(pid):
        return False
    owner_fp = str((identity or {}).get("fingerprint") or "").strip()
    # Legacy supervisors did not record a fingerprint, so one controlled
    # takeover is required when rolling out this contract.
    return not owner_fp or owner_fp != SOURCE_FINGERPRINT


def _retire_lock_owner(identity, timeout=3.0):
    pid = int((identity or {}).get("pid") or 0)
    if not _pid_is_verified_worker(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        if not _pid_is_verified_worker(pid):
            return True
        time.sleep(0.1)
    # A detached stale worker that ignores SIGTERM must not permanently block
    # the new deploy. PID/cmdline/uid were verified above before escalation.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    time.sleep(0.2)
    return not _pid_is_verified_worker(pid)


def _retire_stale_workers():
    retired = []
    for pid in _worker_pids():
        try:
            os.kill(pid, signal.SIGTERM)
            retired.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except Exception:
            pass
    if retired:
        time.sleep(0.4)
    return retired


def claim_singleton_or_exit():
    """Claim the process-level singleton before worker cycles can write state."""
    global LOCK_HANDLE
    if not is_real_worker():
        return {"claimed": False, "reason": "not-worker", "generation": GENERATION}
    try:
        import fcntl
    except ImportError:
        return {"claimed": True, "reason": "no-fcntl", "generation": GENERATION}

    handle = open(LOCK_PATH, "a+")
    acquired = False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except BlockingIOError:
        owner = _read_lock_identity(handle)
        if _should_takeover(owner):
            print(
                f"[RUNTIME_SUPERVISOR] TAKEOVER stale_pid={owner.get('pid',0)} "
                f"old_fp={owner.get('fingerprint','legacy')} new_fp={SOURCE_FINGERPRINT}",
                flush=True,
            )
            _retire_lock_owner(owner)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    time.sleep(0.1)
        if not acquired:
            handle.close()
            print(
                f"[RUNTIME_SUPERVISOR] DUPLICATE_EXIT generation={GENERATION} "
                f"owner_pid={owner.get('pid',0)} owner_fp={owner.get('fingerprint','')}",
                flush=True,
            )
            os._exit(0)

    LOCK_HANDLE = handle
    retired = _retire_stale_workers()
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(
            f"pid={os.getpid()} generation={GENERATION} build={RUNTIME_BUILD} "
            f"fingerprint={SOURCE_FINGERPRINT} started={STARTED_AT}\n"
        )
        handle.flush()
    except Exception:
        pass

    print(
        f"[RUNTIME_SUPERVISOR] CLAIMED pid={os.getpid()} generation={GENERATION} "
        f"build={RUNTIME_BUILD} retired={retired}",
        flush=True,
    )
    return {
        "claimed": True,
        "reason": "singleton",
        "generation": GENERATION,
        "pid": os.getpid(),
        "retired": retired,
        "build": RUNTIME_BUILD,
        "fingerprint": SOURCE_FINGERPRINT,
        "deploy_sha": _DEPLOY_SHA,
        "started_at": STARTED_AT,
    }


CLAIM = claim_singleton_or_exit()
