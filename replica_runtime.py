"""Runtime loops for the read-only UI replica.

The authoritative Render worker periodically mirrors dashboard-facing tables to
Neon. Streamlit periodically pulls the read-only /bootstrap payload from the
Render API and refreshes its local SQLite cache. No production calculation is
performed in the UI process.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

WORKER_REPLICA_SECONDS = max(30, int(os.environ.get("ASI_UI_REPLICA_SECONDS", "60")))
UI_PULL_SECONDS = max(15, int(os.environ.get("ASI_RUNTIME_PULL_SECONDS", "60")))
_PULL_LOCK = threading.Lock()


def sync_worker_once():
    import persistent_bridge
    import ui_replica

    if not persistent_bridge.postgres_enabled():
        return {"status": "DISABLED"}
    persistent_bridge.ensure_remote_schema()
    con = persistent_bridge._pg_connect()
    try:
        result = ui_replica.sync_to_remote(
            con,
            persistent_bridge.LOCAL_DB,
            persistent_bridge.DB_DIR,
            persistent_bridge.BRIDGE_VERSION,
        )
        con.commit()
        return {"status": "SYNCED", **result}
    finally:
        con.close()


def _write_remote_heartbeat(local_db, heartbeat):
    if not heartbeat:
        return
    con = sqlite3.connect(local_db, timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
        mapping = {
            "remote_worker_runtime_build": heartbeat.get("runtime_build", ""),
            "remote_worker_generation": heartbeat.get("worker_generation", ""),
            "remote_worker_pid": heartbeat.get("worker_pid", ""),
            "remote_worker_heartbeat_at": heartbeat.get("heartbeat_at", ""),
            "remote_worker_bridge_version": heartbeat.get("bridge_version", ""),
        }
        for key, value in mapping.items():
            con.execute(
                "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                (key, str(value or "")),
            )
        con.commit()
    finally:
        con.close()


def pull_ui_once():
    """Fetch one authoritative bootstrap payload and refresh the UI cache."""
    import requests
    import persistent_bridge
    import ui_replica

    url = persistent_bridge.runtime_api_url()
    if not url:
        return {"status": "DISABLED"}
    if not _PULL_LOCK.acquire(blocking=False):
        return {"status": "BUSY"}
    try:
        response = requests.get(
            f"{url}/bootstrap",
            timeout=12,
            headers={"User-Agent": "AI-Stock-Intelligence/1.8"},
        )
        response.raise_for_status()
        payload = response.json() or {}
        snapshot = payload.get("opportunity_snapshot") or {}
        core = persistent_bridge._write_local_payload(
            payload.get("state") or {},
            snapshot.get("payload") or [],
            payload.get("premarket_runs") or [],
            payload.get("premarket_picks") or [],
        )
        _write_remote_heartbeat(
            persistent_bridge.LOCAL_DB,
            payload.get("heartbeat") or {},
        )
        ui = ui_replica.write_local(
            payload.get("ui_replica") or {},
            persistent_bridge.LOCAL_DB,
            persistent_bridge.DB_DIR,
        )
        return {"status": "PULLED", "core": core, "ui": ui}
    finally:
        _PULL_LOCK.release()


def _worker_loop():
    # app.py/worker_base.py still need to initialize and populate their tables.
    time.sleep(12)
    while True:
        try:
            print(f"[UI_REPLICA] worker sync {sync_worker_once()}", flush=True)
        except Exception as exc:
            print(f"[UI_REPLICA] worker sync error {exc!r}", flush=True)
        time.sleep(WORKER_REPLICA_SECONDS)


def _ui_loop():
    # sitecustomize performs one synchronous pull before starting this loop.
    time.sleep(UI_PULL_SECONDS)
    while True:
        try:
            result = pull_ui_once()
            if result.get("status") not in {"BUSY"}:
                print(f"[UI_REPLICA] UI pull {result}", flush=True)
        except Exception as exc:
            print(f"[UI_REPLICA] UI pull error {exc!r}", flush=True)
        time.sleep(UI_PULL_SECONDS)


def start_worker_replica():
    names = {t.name for t in threading.enumerate()}
    if "asi-ui-replica-worker" in names:
        return True
    threading.Thread(
        target=_worker_loop,
        name="asi-ui-replica-worker",
        daemon=True,
    ).start()
    return True


def start_ui_pull():
    names = {t.name for t in threading.enumerate()}
    if "asi-ui-runtime-pull" in names:
        return True
    threading.Thread(
        target=_ui_loop,
        name="asi-ui-runtime-pull",
        daemon=True,
    ).start()
    return True
