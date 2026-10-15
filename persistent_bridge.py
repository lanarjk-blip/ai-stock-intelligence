"""Persistent runtime bridge for AI Stock Intelligence.

The existing engine is optimized around local SQLite and local NumPy model
artifacts. Production must not treat those ephemeral files as durable state.
This bridge therefore has two persistence layers:

1. Control plane, synced frequently:
   - state key/value records
   - latest Opportunity snapshot
   - Pre-Market runs and picks
   - worker heartbeat

2. Worker checkpoint, synced less frequently:
   - a transactionally consistent SQLite backup created with sqlite3.backup()
   - model/runtime artifacts from db/ such as *.npz and *.json

The Render worker restores a checkpoint only when its local market database is
missing/cold, so an ordinary process exec cannot roll a healthy local database
back to an older checkpoint. Streamlit restores only the public control plane
through the read-only runtime API and never receives the full database blob.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import date, datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE, "db")
LOCAL_DB = os.path.join(DB_DIR, "market.db")
DATABASE_URL = str(
    os.environ.get("DATABASE_URL")
    or os.environ.get("NEON_DATABASE_URL")
    or ""
).strip()
BRIDGE_VERSION = "PERSIST-BRIDGE-R4-EXACT-MIRROR"
SYNC_SECONDS = max(15, int(os.environ.get("ASI_PERSIST_SYNC_SECONDS", "30")))
CHECKPOINT_SECONDS = max(300, int(os.environ.get("ASI_CHECKPOINT_SECONDS", "900")))
WARM_PRICE_ROWS = max(100, int(os.environ.get("ASI_CHECKPOINT_WARM_PRICE_ROWS", "1000")))
MAX_ARTIFACT_BYTES = max(
    1_000_000,
    int(os.environ.get("ASI_CHECKPOINT_MAX_ARTIFACT_BYTES", str(200 * 1024 * 1024))),
)
_CHECKPOINT_LOCK = threading.Lock()
_LAST_CHECKPOINT_AT = 0.0


def postgres_enabled() -> bool:
    return DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def runtime_api_url() -> str:
    env = str(os.environ.get("ASI_RUNTIME_API_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return str(cfg.get("runtime_api_url") or "").strip().rstrip("/")
    except Exception:
        return ""


def api_enabled() -> bool:
    url = runtime_api_url()
    return url.startswith("https://") or url.startswith("http://")


def enabled() -> bool:
    return postgres_enabled() or api_enabled()


def _pg_connect():
    if not postgres_enabled():
        return None
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=6, autocommit=False)


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        if hasattr(value, "item"):
            value = value.item()
    except Exception:
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _json_records(rows):
    return [
        {str(key): _json_value(value) for key, value in dict(row).items()}
        for row in rows
    ]


def ensure_remote_schema():
    if not postgres_enabled():
        return False
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_opportunity_snapshot (
                singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                generated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                bridge_version TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_premarket_runs (
                market_date TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                pick_count INTEGER NOT NULL,
                note TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_premarket_picks (
                market_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(market_date, rank)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_runtime_heartbeat (
                singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                worker_pid INTEGER,
                worker_generation TEXT,
                runtime_build TEXT,
                heartbeat_at TIMESTAMPTZ NOT NULL,
                bridge_version TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_worker_checkpoint (
                singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                created_at TIMESTAMPTZ NOT NULL,
                sqlite_gzip BYTEA,
                sqlite_sha256 TEXT,
                sqlite_raw_bytes BIGINT,
                artifacts_gzip BYTEA,
                artifacts_sha256 TEXT,
                runtime_build TEXT,
                bridge_version TEXT NOT NULL
            )""")
        con.commit()
        return True
    finally:
        con.close()


def _local_table_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone())


def _local_db_price_rows(path=None):
    db = path or LOCAL_DB
    if not os.path.exists(db):
        return 0
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            if not _local_table_exists(con, "prices"):
                return 0
            row = con.execute("SELECT COUNT(*) FROM prices").fetchone()
            return int(row[0] if row else 0)
        finally:
            con.close()
    except Exception:
        return 0


def _local_db_is_warm():
    return _local_db_price_rows() >= WARM_PRICE_ROWS


def _local_model_artifacts():
    if not os.path.isdir(DB_DIR):
        return []
    out = []
    for name in sorted(os.listdir(DB_DIR)):
        path = os.path.join(DB_DIR, name)
        if not os.path.isfile(path):
            continue
        lower = name.lower()
        if lower.startswith("market.db"):
            continue
        if not lower.endswith((".npz", ".json")):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size <= MAX_ARTIFACT_BYTES:
            out.append(path)
    return out


def _has_model_artifact():
    return any(path.lower().endswith(".npz") for path in _local_model_artifacts())


def _read_local_payload():
    if not os.path.exists(LOCAL_DB):
        return {"state": [], "opportunities": [], "runs": [], "picks": []}
    con = sqlite3.connect(LOCAL_DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        state = (
            [dict(r) for r in con.execute("SELECT key,value FROM state").fetchall()]
            if _local_table_exists(con, "state") else []
        )
        opportunities = (
            [dict(r) for r in con.execute("SELECT * FROM opportunities ORDER BY rank").fetchall()]
            if _local_table_exists(con, "opportunities") else []
        )
        runs = (
            [dict(r) for r in con.execute(
                "SELECT market_date,generated_at,status,pick_count,note "
                "FROM premarket_runs ORDER BY market_date DESC LIMIT 20"
            ).fetchall()]
            if _local_table_exists(con, "premarket_runs") else []
        )
        picks = (
            [dict(r) for r in con.execute(
                "SELECT * FROM premarket_picks WHERE market_date IN "
                "(SELECT market_date FROM premarket_runs ORDER BY market_date DESC LIMIT 20) "
                "ORDER BY market_date,rank"
            ).fetchall()]
            if _local_table_exists(con, "premarket_picks")
            and _local_table_exists(con, "premarket_runs") else []
        )
        return {
            "state": state,
            "opportunities": opportunities,
            "runs": runs,
            "picks": picks,
        }
    finally:
        con.close()


def sync_local_to_remote(runtime_identity=None):
    if not postgres_enabled():
        return {"status": "DISABLED"}
    ensure_remote_schema()
    data = _read_local_payload()
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            for row in data["state"]:
                cur.execute(
                    """INSERT INTO asi_state(key,value,updated_at)
                       VALUES(%s,%s,now())
                       ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value,updated_at=excluded.updated_at""",
                    (str(row.get("key") or ""), str(row.get("value") or "")),
                )
            if data["opportunities"]:
                snapshot_generated_at = str(
                    data["opportunities"][0].get("updated_at")
                    or data["opportunities"][0].get("generated_at")
                    or datetime.now(timezone.utc).isoformat()
                )
                cur.execute(
                    """INSERT INTO asi_opportunity_snapshot
                       (singleton,generated_at,payload,bridge_version)
                       VALUES(1,%s,%s::jsonb,%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                       generated_at=excluded.generated_at,
                       payload=excluded.payload,
                       bridge_version=excluded.bridge_version""",
                    (
                        snapshot_generated_at,
                        json.dumps(_json_records(data["opportunities"]), ensure_ascii=False),
                        BRIDGE_VERSION,
                    ),
                )
            else:
                # Exact mirror semantics: an explicitly empty local snapshot must
                # not leave an old remote snapshot available for a later restore.
                cur.execute("DELETE FROM asi_opportunity_snapshot WHERE singleton=1")

            # H-10 is immutable across workers too. A stale/provisional local
            # worker must never downgrade a remote H10_FINAL run.
            cur.execute("SELECT market_date,note FROM asi_premarket_runs")
            remote_final_dates = {
                str(r[0]) for r in cur.fetchall()
                if str(r[1] or "").startswith("H10_FINAL")
            }
            local_run_dates = set()
            local_pick_counts = {}
            for row in data["runs"]:
                market_date = str(row.get("market_date") or "")
                if not market_date:
                    continue
                note = str(row.get("note") or "")
                local_final = note.startswith("H10_FINAL")
                if market_date in remote_final_dates and not local_final:
                    continue
                pick_count = max(0, int(row.get("pick_count") or 0))
                cur.execute(
                    """INSERT INTO asi_premarket_runs
                       (market_date,generated_at,status,pick_count,note,updated_at)
                       VALUES(%s,%s,%s,%s,%s,now())
                       ON CONFLICT(market_date) DO UPDATE SET
                       generated_at=excluded.generated_at,
                       status=excluded.status,
                       pick_count=excluded.pick_count,
                       note=excluded.note,
                       updated_at=excluded.updated_at""",
                    (
                        market_date,
                        str(row.get("generated_at") or ""),
                        str(row.get("status") or ""),
                        pick_count,
                        note,
                    ),
                )
                local_run_dates.add(market_date)
                local_pick_counts[market_date] = pick_count

            # Replace, do not merely upsert, each mirrored date. This removes
            # ghost rank 4/5 rows when a valid run shrinks to fewer picks or zero.
            for market_date in sorted(local_run_dates):
                cur.execute(
                    "DELETE FROM asi_premarket_picks WHERE market_date=%s",
                    (market_date,),
                )

            for row in data["picks"]:
                market_date = str(row.get("market_date") or "")
                if market_date not in local_run_dates:
                    continue
                rank = int(row.get("rank") or 0)
                pick_count = int(local_pick_counts.get(market_date, 0))
                if rank < 1 or rank > pick_count:
                    continue
                cur.execute(
                    """INSERT INTO asi_premarket_picks
                       (market_date,rank,payload,updated_at)
                       VALUES(%s,%s,%s::jsonb,now())
                       ON CONFLICT(market_date,rank) DO UPDATE SET
                       payload=excluded.payload,updated_at=excluded.updated_at""",
                    (
                        market_date,
                        rank,
                        json.dumps(
                            {str(k): _json_value(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                    ),
                )
            if runtime_identity:
                cur.execute(
                    """INSERT INTO asi_runtime_heartbeat
                       (singleton,worker_pid,worker_generation,runtime_build,heartbeat_at,bridge_version)
                       VALUES(1,%s,%s,%s,now(),%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                       worker_pid=excluded.worker_pid,
                       worker_generation=excluded.worker_generation,
                       runtime_build=excluded.runtime_build,
                       heartbeat_at=excluded.heartbeat_at,
                       bridge_version=excluded.bridge_version""",
                    (
                        int(runtime_identity.get("pid") or 0),
                        str(runtime_identity.get("generation") or ""),
                        str(runtime_identity.get("build") or ""),
                        BRIDGE_VERSION,
                    ),
                )
        con.commit()
        return {
            "status": "SYNCED",
            "state": len(data["state"]),
            "opportunities": len(data["opportunities"]),
            "runs": len(data["runs"]),
            "picks": len(data["picks"]),
        }
    finally:
        con.close()


def _consistent_sqlite_bytes():
    """Return a gzip-compressed transactionally consistent SQLite backup."""
    if not os.path.exists(LOCAL_DB):
        return None, None, 0
    os.makedirs(DB_DIR, exist_ok=True)
    fd, backup_path = tempfile.mkstemp(prefix="asi-checkpoint-", suffix=".db", dir=DB_DIR)
    os.close(fd)
    try:
        source = sqlite3.connect(LOCAL_DB, timeout=60)
        target = sqlite3.connect(backup_path, timeout=60)
        try:
            source.execute("PRAGMA busy_timeout=60000")
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        with open(backup_path, "rb") as fh:
            raw = fh.read()
        checksum = hashlib.sha256(raw).hexdigest()
        return gzip.compress(raw, compresslevel=6), checksum, len(raw)
    finally:
        try:
            os.remove(backup_path)
        except OSError:
            pass


def _artifact_archive_bytes():
    """Return a gzipped tar of non-SQLite model/runtime artifacts."""
    paths = _local_model_artifacts()
    if not paths:
        return None, None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in paths:
            tar.add(path, arcname=os.path.basename(path), recursive=False)
    payload = buf.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


def _checkpoint_due(force=False):
    if force:
        return True
    return (time.monotonic() - _LAST_CHECKPOINT_AT) >= CHECKPOINT_SECONDS


def sync_worker_checkpoint(runtime_identity=None, force=False):
    """Persist full worker data only after the local engine is meaningfully warm."""
    global _LAST_CHECKPOINT_AT
    if not postgres_enabled():
        return {"status": "DISABLED"}
    if not _checkpoint_due(force=force):
        return {"status": "NOT_DUE"}
    price_rows = _local_db_price_rows()
    if price_rows < WARM_PRICE_ROWS:
        return {
            "status": "SKIP_COLD",
            "price_rows": price_rows,
            "required": WARM_PRICE_ROWS,
        }

    if not _CHECKPOINT_LOCK.acquire(blocking=False):
        return {"status": "BUSY"}
    try:
        sqlite_gzip, sqlite_sha, sqlite_raw_bytes = _consistent_sqlite_bytes()
        if not sqlite_gzip:
            return {"status": "NO_DB"}
        artifacts_gzip, artifacts_sha = _artifact_archive_bytes()
        ensure_remote_schema()
        con = _pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute(
                    """INSERT INTO asi_worker_checkpoint
                       (singleton,created_at,sqlite_gzip,sqlite_sha256,sqlite_raw_bytes,
                        artifacts_gzip,artifacts_sha256,runtime_build,bridge_version)
                       VALUES(1,now(),%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(singleton) DO UPDATE SET
                       created_at=excluded.created_at,
                       sqlite_gzip=excluded.sqlite_gzip,
                       sqlite_sha256=excluded.sqlite_sha256,
                       sqlite_raw_bytes=excluded.sqlite_raw_bytes,
                       artifacts_gzip=excluded.artifacts_gzip,
                       artifacts_sha256=excluded.artifacts_sha256,
                       runtime_build=excluded.runtime_build,
                       bridge_version=excluded.bridge_version""",
                    (
                        sqlite_gzip,
                        sqlite_sha,
                        int(sqlite_raw_bytes),
                        artifacts_gzip,
                        artifacts_sha,
                        str((runtime_identity or {}).get("build") or ""),
                        BRIDGE_VERSION,
                    ),
                )
            con.commit()
        finally:
            con.close()
        _LAST_CHECKPOINT_AT = time.monotonic()
        return {
            "status": "CHECKPOINTED",
            "price_rows": price_rows,
            "sqlite_raw_bytes": sqlite_raw_bytes,
            "sqlite_gzip_bytes": len(sqlite_gzip),
            "artifacts_gzip_bytes": len(artifacts_gzip or b""),
            "sqlite_sha256": sqlite_sha,
        }
    finally:
        _CHECKPOINT_LOCK.release()


def _validate_sqlite_file(path):
    try:
        con = sqlite3.connect(path, timeout=10)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            return bool(row and str(row[0]).lower() == "ok")
        finally:
            con.close()
    except Exception:
        return False


def _remove_sqlite_sidecars(path=None):
    """Remove stale WAL/SHM sidecars before replacing a cold SQLite database."""
    base = path or LOCAL_DB
    removed = []
    for suffix in ("-wal", "-shm"):
        sidecar = base + suffix
        try:
            os.remove(sidecar)
            removed.append(sidecar)
        except FileNotFoundError:
            pass
    return removed


def _restore_sqlite_blob(sqlite_gzip, expected_sha):
    if sqlite_gzip is None:
        return False
    raw = gzip.decompress(bytes(sqlite_gzip))
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and actual_sha != str(expected_sha):
        raise ValueError("worker checkpoint SQLite checksum mismatch")
    os.makedirs(DB_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="asi-restore-", suffix=".db", dir=DB_DIR)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        if not _validate_sqlite_file(temp_path):
            raise ValueError("worker checkpoint SQLite quick_check failed")
        _remove_sqlite_sidecars(LOCAL_DB)
        os.replace(temp_path, LOCAL_DB)
        _remove_sqlite_sidecars(LOCAL_DB)
        temp_path = ""
        return True
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _restore_artifacts_blob(artifacts_gzip, expected_sha):
    if not artifacts_gzip:
        return 0
    payload = bytes(artifacts_gzip)
    actual_sha = hashlib.sha256(payload).hexdigest()
    if expected_sha and actual_sha != str(expected_sha):
        raise ValueError("worker checkpoint artifact checksum mismatch")
    os.makedirs(DB_DIR, exist_ok=True)
    restored = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Never trust archive paths from storage, even storage we own.
            if member.name != os.path.basename(member.name):
                continue
            lower = member.name.lower()
            if not lower.endswith((".npz", ".json")):
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            data = source.read(MAX_ARTIFACT_BYTES + 1)
            if len(data) > MAX_ARTIFACT_BYTES:
                continue
            target = os.path.join(DB_DIR, member.name)
            fd, temp_path = tempfile.mkstemp(prefix="asi-artifact-", dir=DB_DIR)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp_path, target)
                temp_path = ""
                restored += 1
            finally:
                if temp_path:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
    return restored


def restore_worker_checkpoint_if_needed(force=False):
    """Restore full DB/model only on a cold worker, never over a warm local DB."""
    if not postgres_enabled():
        return {"status": "DISABLED"}
    local_rows = _local_db_price_rows()
    need_db = bool(force or local_rows < WARM_PRICE_ROWS)
    need_artifacts = bool(force or not _has_model_artifact())
    if not need_db and not need_artifacts:
        return {"status": "LOCAL_WARM", "price_rows": local_rows}

    ensure_remote_schema()
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute(
                """SELECT created_at,sqlite_gzip,sqlite_sha256,sqlite_raw_bytes,
                          artifacts_gzip,artifacts_sha256,runtime_build,bridge_version
                   FROM asi_worker_checkpoint WHERE singleton=1"""
            )
            row = cur.fetchone()
        con.rollback()
    finally:
        con.close()
    if not row:
        return {"status": "NO_CHECKPOINT", "price_rows": local_rows}

    (
        created_at,
        sqlite_gzip,
        sqlite_sha,
        sqlite_raw_bytes,
        artifacts_gzip,
        artifacts_sha,
        runtime_build,
        bridge_version,
    ) = row
    db_restored = False
    artifact_count = 0
    if need_db and sqlite_gzip:
        db_restored = _restore_sqlite_blob(sqlite_gzip, sqlite_sha)
    if need_artifacts and artifacts_gzip:
        artifact_count = _restore_artifacts_blob(artifacts_gzip, artifacts_sha)

    restored_rows = _local_db_price_rows()
    try:
        con = sqlite3.connect(LOCAL_DB, timeout=10)
        con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
        markers = {
            "persistent_checkpoint_restored_at": datetime.now(timezone.utc).isoformat(),
            "persistent_checkpoint_source_at": (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ),
            "persistent_checkpoint_runtime_build": str(runtime_build or ""),
            "persistent_checkpoint_bridge_version": str(bridge_version or ""),
            "persistent_checkpoint_db_restored": str(bool(db_restored)),
            "persistent_checkpoint_artifacts_restored": str(int(artifact_count)),
        }
        for key, value in markers.items():
            con.execute(
                "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                (key, str(value)),
            )
        con.commit()
        con.close()
    except Exception:
        pass

    return {
        "status": "RESTORED_CHECKPOINT",
        "checkpoint_at": (
            created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
        ),
        "db_restored": db_restored,
        "artifacts_restored": artifact_count,
        "price_rows": restored_rows,
        "sqlite_raw_bytes": int(sqlite_raw_bytes or 0),
    }


def _ensure_local_core(con):
    con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_date TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        rank INTEGER,
        ticker TEXT,
        company TEXT,
        decision TEXT,
        confidence REAL,
        ai_score REAL,
        risk TEXT,
        reference_price REAL,
        entry_low REAL,
        entry_high REAL,
        stop_loss REAL,
        target_1 REAL,
        target_2 REAL,
        ihsg_regime TEXT,
        reason TEXT,
        source_phase TEXT,
        UNIQUE(market_date, rank)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
        market_date TEXT PRIMARY KEY,
        generated_at TEXT NOT NULL,
        status TEXT NOT NULL,
        pick_count INTEGER NOT NULL,
        note TEXT
    )""")
    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(premarket_picks)").fetchall()}
    if "reference_price" not in existing:
        con.execute("ALTER TABLE premarket_picks ADD COLUMN reference_price REAL")


def _write_local_payload(state, opportunities, runs, picks):
    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(LOCAL_DB, timeout=30)
    try:
        _ensure_local_core(con)
        if isinstance(state, dict):
            state = [{"key": k, "value": v} for k, v in state.items()]
        for row in state or []:
            con.execute(
                "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                (str(row.get("key") or ""), str(row.get("value") or "")),
            )
        if opportunities:
            import pandas as pd
            frame = pd.DataFrame(list(opportunities))
            if not frame.empty:
                frame.to_sql("opportunities", con, if_exists="replace", index=False)
        elif _local_table_exists(con, "opportunities"):
            # Remote explicitly has no snapshot. Do not preserve a stale local one.
            con.execute("DELETE FROM opportunities")

        accepted_run_dates = set()
        incoming_pick_counts = {}
        for row in runs or []:
            market_date = str(row.get("market_date") or "")
            if not market_date:
                continue
            note = str(row.get("note") or "")
            existing = con.execute(
                "SELECT note FROM premarket_runs WHERE market_date=?",
                (market_date,),
            ).fetchone()
            if existing and str(existing[0] or "").startswith("H10_FINAL") and not note.startswith("H10_FINAL"):
                continue
            pick_count = max(0, int(row.get("pick_count") or 0))
            con.execute(
                "INSERT OR REPLACE INTO premarket_runs"
                "(market_date,generated_at,status,pick_count,note) VALUES(?,?,?,?,?)",
                (
                    market_date,
                    str(row.get("generated_at") or ""),
                    str(row.get("status") or ""),
                    pick_count,
                    note,
                ),
            )
            accepted_run_dates.add(market_date)
            incoming_pick_counts[market_date] = pick_count

        for market_date in accepted_run_dates:
            con.execute("DELETE FROM premarket_picks WHERE market_date=?", (market_date,))

        for row in picks or []:
            market_date = str(row.get("market_date") or "")
            if market_date not in accepted_run_dates:
                continue
            rank = int(row.get("rank") or 0)
            if rank < 1 or rank > int(incoming_pick_counts.get(market_date, 0)):
                continue
            cols = [
                "market_date", "generated_at", "rank", "ticker", "company",
                "decision", "confidence", "ai_score", "risk", "reference_price",
                "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
                "ihsg_regime", "reason", "source_phase",
            ]
            con.execute(
                """INSERT OR REPLACE INTO premarket_picks
                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [row.get(col) for col in cols],
            )
        con.commit()
        return {
            "state": len(state or []),
            "opportunities": len(opportunities or []),
            "runs": len(runs or []),
            "picks": len(picks or []),
        }
    finally:
        con.close()


def restore_local_from_postgres():
    ensure_remote_schema()
    con_pg = _pg_connect()
    try:
        with con_pg.cursor() as cur:
            cur.execute("SELECT key,value FROM asi_state")
            state = [{"key": r[0], "value": r[1]} for r in cur.fetchall()]
            cur.execute("SELECT payload FROM asi_opportunity_snapshot WHERE singleton=1")
            snap = cur.fetchone()
            cur.execute(
                "SELECT market_date,generated_at,status,pick_count,note "
                "FROM asi_premarket_runs ORDER BY market_date"
            )
            runs = [
                {
                    "market_date": r[0],
                    "generated_at": r[1],
                    "status": r[2],
                    "pick_count": r[3],
                    "note": r[4],
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                """SELECT p.payload FROM asi_premarket_picks p
                   JOIN asi_premarket_runs r ON r.market_date=p.market_date
                   WHERE p.rank BETWEEN 1 AND r.pick_count
                   ORDER BY p.market_date,p.rank"""
            )
            picks = [
                r[0] if isinstance(r[0], dict) else json.loads(r[0])
                for r in cur.fetchall()
            ]
        con_pg.rollback()
    finally:
        con_pg.close()
    opportunities = []
    if snap and snap[0]:
        opportunities = snap[0] if isinstance(snap[0], list) else json.loads(snap[0])
    counts = _write_local_payload(state, opportunities, runs, picks)
    return {"status": "RESTORED_POSTGRES", **counts}


def restore_local_from_api():
    import requests
    url = runtime_api_url()
    if not url:
        return {"status": "DISABLED"}
    response = requests.get(
        f"{url}/bootstrap",
        timeout=8,
        headers={"User-Agent": "AI-Stock-Intelligence/1.8"},
    )
    response.raise_for_status()
    payload = response.json() or {}
    snapshot = payload.get("opportunity_snapshot") or {}
    counts = _write_local_payload(
        payload.get("state") or {},
        snapshot.get("payload") or [],
        payload.get("premarket_runs") or [],
        payload.get("premarket_picks") or [],
    )
    heartbeat = payload.get("heartbeat") or {}
    if heartbeat:
        os.makedirs(DB_DIR, exist_ok=True)
        con = sqlite3.connect(LOCAL_DB, timeout=10)
        try:
            _ensure_local_core(con)
            for key, value in {
                "remote_worker_runtime_build": heartbeat.get("runtime_build", ""),
                "remote_worker_generation": heartbeat.get("worker_generation", ""),
                "remote_worker_pid": heartbeat.get("worker_pid", ""),
                "remote_worker_heartbeat_at": heartbeat.get("heartbeat_at", ""),
            }.items():
                con.execute(
                    "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                    (key, str(value or "")),
                )
            con.commit()
        finally:
            con.close()
    return {"status": "RESTORED_API", **counts}


def restore_local_from_remote():
    if postgres_enabled():
        return restore_local_from_postgres()
    if api_enabled():
        return restore_local_from_api()
    return {"status": "DISABLED"}


def run_forever(runtime_identity=None):
    """Continuously mirror control-plane state and periodically checkpoint data-plane state."""
    if not postgres_enabled():
        return
    # sitecustomize already restores synchronously before app.py imports. Give
    # app.py/worker_base.py a moment to create their schemas before first sync.
    time.sleep(8)
    while True:
        try:
            result = sync_local_to_remote(runtime_identity=runtime_identity)
            print(f"[PERSIST_BRIDGE] sync {result}", flush=True)
        except Exception as exc:
            print(f"[PERSIST_BRIDGE] sync error {exc!r}", flush=True)
        try:
            checkpoint = sync_worker_checkpoint(runtime_identity=runtime_identity)
            if checkpoint.get("status") not in {"NOT_DUE", "SKIP_COLD"}:
                print(f"[PERSIST_BRIDGE] checkpoint {checkpoint}", flush=True)
        except Exception as exc:
            print(f"[PERSIST_BRIDGE] checkpoint error {exc!r}", flush=True)
        time.sleep(SYNC_SECONDS)


def start_worker_bridge(runtime_identity=None):
    if not postgres_enabled():
        return False
    names = {t.name for t in threading.enumerate()}
    if "asi-persist-bridge" in names:
        return True
    threading.Thread(
        target=run_forever,
        args=(runtime_identity,),
        name="asi-persist-bridge",
        daemon=True,
    ).start()
    return True
