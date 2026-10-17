"""Fast fail-safe startup self-check for production worker state."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
DB = os.path.join(BASE, "db", "market.db")


def run():
    result = {"status": "READY", "checks": {}, "reasons": []}
    try:
        cfg = json.loads(open(CFG_PATH, "r", encoding="utf-8").read())
        version = str(cfg.get("mobile_ui_version") or "")
        result["checks"]["version"] = version
        if not version:
            result["reasons"].append("mobile_ui_version missing")
        for key in (
            "data_quality_gate_enabled",
            "housekeeping_enabled",
            "housekeeping_intraday_retention_days",
            "housekeeping_engine_log_retention_days",
        ):
            if key not in cfg:
                result["reasons"].append(f"config missing {key}")
    except Exception as exc:
        result["reasons"].append(f"config error: {exc}")
        cfg = {}

    if not os.path.exists(DB):
        result["reasons"].append("database missing")
    else:
        try:
            con = sqlite3.connect(DB, timeout=15)
            quick = con.execute("PRAGMA quick_check").fetchone()
            result["checks"]["db_quick_check"] = quick[0] if quick else "unknown"
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required = {"prices", "state", "market_index"}
            missing = sorted(required - tables)
            if missing:
                result["reasons"].append("missing tables: " + ",".join(missing))
            con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
            status = "READY" if not result["reasons"] else "DEGRADED"
            now = datetime.now(timezone.utc).isoformat()
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", ("startup_selfcheck_at", now))
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", ("startup_selfcheck_status", status))
            con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", ("startup_selfcheck_reasons", " | ".join(result["reasons"])))
            con.commit(); con.close()
        except Exception as exc:
            result["reasons"].append(f"database error: {exc}")

    result["status"] = "READY" if not result["reasons"] else "DEGRADED"
    return result
