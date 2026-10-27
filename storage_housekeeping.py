"""Safe automatic retention and SQLite housekeeping.

Only explicitly disposable tables are pruned automatically. Historical
recommendations, validation, shadow portfolio, model artefacts and protected
business records are never deleted by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json
import os
import sqlite3
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "db", "market.db")
CFG_PATH = os.path.join(BASE, "config.json")
BACKUP_DIR = os.path.join(BASE, "db", "backups")
WIB = ZoneInfo("Asia/Jakarta")

PROTECTED_TABLES = {
    "timemap_forecast_log", "timemap_forecast_outcomes", "timemap_forecast_watch", "timemap_forecast_eval_state", "timemap_forecast_prepare_state",
    "prices", "market_index", "state", "universe_registry",
    "recommendation_history", "shadow_portfolio", "premarket_picks",
    "premarket_runs", "dynamic_opportunities", "model_validation",
}
DISPOSABLE_RULES = {
    "intraday": ("ts", "housekeeping_intraday_retention_days", 60),
    "engine_log": ("ts", "housekeeping_engine_log_retention_days", 30),
}


def _cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _connect():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _table_exists(con, table):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _state_get(con, key):
    if not _table_exists(con, "state"):
        return None
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _state_set(con, key, value):
    con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
    con.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (str(key), str(value)))


def storage_health():
    cfg = _cfg()
    size = os.path.getsize(DB) if os.path.exists(DB) else 0
    mb = size / (1024 * 1024)
    warn = float(cfg.get("housekeeping_storage_warn_mb", 250))
    critical = float(cfg.get("housekeeping_storage_critical_mb", 500))
    status = "CRITICAL" if mb >= critical else ("WARNING" if mb >= warn else "NORMAL")
    last = None
    try:
        con = _connect(); last = _state_get(con, "housekeeping_last_completed_at"); con.close()
    except Exception:
        pass
    label = None
    if last:
        try:
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            label = dt.astimezone(WIB).strftime("%d-%m %H:%M WIB")
        except Exception:
            label = str(last)[:16]
    return {"status": status, "db_mb": mb, "last_cleanup": last, "last_cleanup_label": label}


def plan_cleanup(now=None):
    cfg = _cfg(); current = now or datetime.now(timezone.utc)
    plan = []
    if not os.path.exists(DB):
        return plan
    con = _connect()
    try:
        for table, (column, key, default_days) in DISPOSABLE_RULES.items():
            if table in PROTECTED_TABLES or not _table_exists(con, table):
                continue
            days = max(1, int(cfg.get(key, default_days)))
            cutoff = (current - timedelta(days=days)).isoformat()
            count = int(con.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} < ?", (cutoff,)).fetchone()[0])
            plan.append({"table": table, "column": column, "cutoff": cutoff, "days": days, "rows": count})
    finally:
        con.close()
    return plan


def _backup_database():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(BACKUP_DIR, f"market-before-cleanup-{stamp}.sqlite")
    src = _connect(); dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    return path


def _prune_old_backups(days):
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = time.time() - max(1, int(days)) * 86400
    for name in os.listdir(BACKUP_DIR):
        path = os.path.join(BACKUP_DIR, name)
        try:
            if name.startswith("market-before-cleanup-") and os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def run_cleanup(*, dry_run=None, force=False):
    cfg = _cfg()
    if not bool(cfg.get("housekeeping_enabled", True)) and not force:
        return {"status": "DISABLED", "deleted": 0, "plan": []}
    if dry_run is None:
        dry_run = bool(cfg.get("housekeeping_dry_run", False))
    plan = plan_cleanup()
    total = sum(int(x["rows"]) for x in plan)
    if dry_run or total <= 0:
        return {"status": "DRY_RUN" if dry_run else "NOOP", "deleted": 0, "planned": total, "plan": plan}

    con = _connect()
    try:
        check = con.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            return {"status": "ABORT_INTEGRITY", "deleted": 0, "planned": total, "plan": plan}
    finally:
        con.close()

    backup = _backup_database()
    deleted = 0
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        for item in plan:
            table = item["table"]
            if table in PROTECTED_TABLES or table not in DISPOSABLE_RULES:
                raise RuntimeError(f"blocked table: {table}")
            cur = con.execute(f"DELETE FROM {table} WHERE {item['column']} < ?", (item["cutoff"],))
            deleted += max(0, int(cur.rowcount or 0))
        now = datetime.now(timezone.utc).isoformat()
        _state_set(con, "housekeeping_last_completed_at", now)
        _state_set(con, "housekeeping_last_deleted_rows", deleted)
        _state_set(con, "housekeeping_last_backup", backup)
        con.commit()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

    if bool(cfg.get("housekeeping_vacuum_enabled", True)) and deleted >= int(cfg.get("housekeeping_vacuum_min_deleted_rows", 5000)):
        try:
            con = _connect(); con.execute("VACUUM"); con.close()
        except Exception:
            pass
    _prune_old_backups(int(cfg.get("housekeeping_backup_retention_days", 7)))
    return {"status": "CLEANED", "deleted": deleted, "planned": total, "plan": plan, "backup": backup}


def _market_is_live():
    try:
        import market_session
        status = market_session.session_status(_cfg())
        return bool(status.get("live")) or str(status.get("status") or "").upper() == "BREAK"
    except Exception:
        return False


def run_forever():
    time.sleep(45)
    while True:
        try:
            cfg = _cfg()
            interval = max(1, int(cfg.get("housekeeping_interval_hours", 24)))
            due = True
            try:
                con = _connect(); last = _state_get(con, "housekeeping_last_completed_at"); con.close()
                if last:
                    dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    due = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)) >= timedelta(hours=interval)
            except Exception:
                due = True
            if due and not _market_is_live():
                result = run_cleanup()
                print(f"[HOUSEKEEPING] {result}", flush=True)
        except Exception as exc:
            print(f"[HOUSEKEEPING] error: {exc!r}", flush=True)
        time.sleep(3600)
