"""Safe deployment preflight for AI Stock Intelligence.

Validates host configuration and Neon persistence without ever printing
credentials. The command can also require proof that a worker has already
written runtime state, which is the final distinction between "deployable"
and "actually live".
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

import persistent_bridge
import runtime_host

PREFLIGHT_VERSION = "DEPLOY-PREFLIGHT-R1"


def _safe_error(exc: BaseException) -> str:
    return type(exc).__name__


def _db_probe() -> Dict[str, Any]:
    if not persistent_bridge.postgres_enabled():
        return {
            "ok": False,
            "status": "DATABASE_URL_MISSING",
            "schema_ok": False,
            "runtime_seen": False,
        }

    try:
        persistent_bridge.ensure_remote_schema()
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute(
                    """SELECT
                        (SELECT count(*) FROM asi_runtime_heartbeat),
                        (SELECT count(*) FROM asi_state),
                        (SELECT count(*) FROM asi_opportunity_snapshot),
                        (SELECT count(*) FROM asi_premarket_runs),
                        (SELECT count(*) FROM asi_worker_checkpoint)
                    """
                )
                row = cur.fetchone() or (0, 0, 0, 0, 0)
            con.rollback()
        finally:
            con.close()
    except Exception as exc:
        return {
            "ok": False,
            "status": "DATABASE_UNAVAILABLE",
            "schema_ok": False,
            "runtime_seen": False,
            "error_type": _safe_error(exc),
        }

    counts = {
        "heartbeat": int(row[0] or 0),
        "state": int(row[1] or 0),
        "opportunity_snapshot": int(row[2] or 0),
        "premarket_runs": int(row[3] or 0),
        "worker_checkpoint": int(row[4] or 0),
    }
    return {
        "ok": True,
        "status": "DATABASE_READY",
        "schema_ok": True,
        "counts": counts,
        "runtime_seen": bool(counts["heartbeat"] or counts["state"]),
    }


def run_preflight(env: Dict[str, str] | None = None) -> Dict[str, Any]:
    source = dict(os.environ if env is None else env)
    try:
        host = runtime_host.validate_environment(source)
        host_payload = {
            "ok": True,
            "status": "HOST_CONFIG_READY",
            "port": str(host.get("port") or ""),
        }
    except Exception as exc:
        host_payload = {
            "ok": False,
            "status": "HOST_CONFIG_INVALID",
            "port": str(source.get("PORT") or ""),
            "error_type": _safe_error(exc),
        }

    db = _db_probe()
    return {
        "version": PREFLIGHT_VERSION,
        "ok": bool(host_payload["ok"] and db.get("ok")),
        "host": host_payload,
        "database": db,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI Stock Intelligence deployment preflight")
    parser.add_argument(
        "--require-live-runtime",
        action="store_true",
        help="require worker heartbeat/state evidence in Neon",
    )
    args = parser.parse_args(argv)
    result = run_preflight()
    if args.require_live_runtime:
        live_ok = bool(result.get("database", {}).get("runtime_seen"))
        result["live_runtime_required"] = True
        result["live_runtime_ok"] = live_ok
        result["ok"] = bool(result.get("ok") and live_ok)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
