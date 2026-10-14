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

RUNTIME_BUILD = "V1.8.52-20261015-R21-SINGLETON"
SUPERVISOR_VERSION = "RUNTIME-SUPERVISOR-R1"
LOCK_PATH = "/tmp/ai_stock_intelligence_worker.singleton.lock"
LOCK_HANDLE = None
GENERATION = uuid.uuid4().hex[:12]
STARTED_AT = datetime.now(timezone.utc).isoformat()


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
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        print(
            f"[RUNTIME_SUPERVISOR] DUPLICATE_EXIT generation={GENERATION}",
            flush=True,
        )
        os._exit(0)

    LOCK_HANDLE = handle
    retired = _retire_stale_workers()
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(
            f"pid={os.getpid()} generation={GENERATION} build={RUNTIME_BUILD} started={STARTED_AT}\n"
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
        "started_at": STARTED_AT,
    }


CLAIM = claim_singleton_or_exit()
