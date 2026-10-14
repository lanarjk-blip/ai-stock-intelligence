"""Prevent Streamlit from spawning a competing local production worker.

app.py predates the external Render runtime and automatically calls
subprocess.Popen(... worker_base.py ...).  Once a read-only runtime API is
configured, Render is the sole production engine and Streamlit is only a UI.
This narrowly wraps subprocess.Popen and suppresses only worker_base.py; every
other subprocess call is delegated unchanged.
"""
from __future__ import annotations

import os
import subprocess

_INSTALLED = False
_ORIGINAL_POPEN = subprocess.Popen


class _ExternalWorkerHandle:
    pid = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        return None

    def kill(self):
        return None


def _is_worker_launch(args):
    try:
        if isinstance(args, (list, tuple)):
            parts = [str(x) for x in args]
        else:
            parts = [str(args)]
        return any(os.path.basename(part) == "worker_base.py" for part in parts)
    except Exception:
        return False


def install():
    global _INSTALLED
    if _INSTALLED:
        return

    def guarded_popen(args, *popen_args, **popen_kwargs):
        if _is_worker_launch(args):
            print(
                "[EXTERNAL_RUNTIME] local worker spawn suppressed; Render runtime is authoritative",
                flush=True,
            )
            return _ExternalWorkerHandle()
        return _ORIGINAL_POPEN(args, *popen_args, **popen_kwargs)

    guarded_popen._asi_external_runtime_guard = True
    subprocess.Popen = guarded_popen
    _INSTALLED = True
