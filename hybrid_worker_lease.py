"""Distributed worker lease for laptop/HP/Render failover.

Only one market worker may publish at a time across devices. The local
runtime_supervisor still prevents duplicate processes on one machine; this
module adds a Neon-backed lease across machines.

The lease is intentionally sticky: a healthy current holder is never preempted
mid-cycle merely because a higher-priority device appears. Device priority is
used as a startup bias (Render -> laptop -> HP). This avoids split-brain writes
while still making laptop the usual primary and HP the standby.
"""
from __future__ import annotations

import atexit
import os
import socket
import threading
import time

LEASE_VERSION = "HYBRID-LEASE-R1"
LEASE_TTL_SECONDS = max(90, int(os.environ.get("ASI_HYBRID_LEASE_TTL_SECONDS", "180")))
RENEW_SECONDS = max(15, min(60, int(os.environ.get("ASI_HYBRID_RENEW_SECONDS", "30"))))
POLL_SECONDS = max(5, min(60, int(os.environ.get("ASI_HYBRID_POLL_SECONDS", "20"))))

_DEVICE_PRIORITY = {"render": 300, "laptop": 200, "hp": 100}
_DEVICE_DELAY = {"render": 0.0, "laptop": 2.0, "hp": 8.0}
_RENEW_THREAD = None
_STOP = threading.Event()
_ACTIVE_IDENTITY = None
_LAST_RENEW_MONOTONIC = None


def enabled() -> bool:
    return str(os.environ.get("ASI_HYBRID_WORKER") or "").strip() == "1"


def database_url() -> str:
    return str(
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or ""
    ).strip()


def detect_device_type(env=None) -> str:
    e = os.environ if env is None else env
    explicit = str(e.get("ASI_WORKER_DEVICE") or "").strip().lower()
    if explicit in _DEVICE_PRIORITY:
        return explicit
    if e.get("RENDER") or e.get("RENDER_SERVICE_ID") or e.get("RENDER_INSTANCE_ID"):
        return "render"
    prefix = str(e.get("PREFIX") or "").lower()
    if e.get("ANDROID_ROOT") or "com.termux" in prefix or "/data/data/com.termux" in prefix:
        return "hp"
    return "laptop"


def device_priority(device_type=None) -> int:
    return int(_DEVICE_PRIORITY.get(device_type or detect_device_type(), 50))


def startup_delay_seconds(device_type=None) -> float:
    raw = os.environ.get("ASI_HYBRID_CLAIM_DELAY_SECONDS")
    if raw not in (None, ""):
        try:
            return max(0.0, min(30.0, float(raw)))
        except Exception:
            pass
    return float(_DEVICE_DELAY.get(device_type or detect_device_type(), 10.0))


def _pg_connect():
    url = database_url()
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        raise SystemExit(
            "Hybrid worker requires DATABASE_URL/NEON_DATABASE_URL pointing to PostgreSQL."
        )
    import psycopg
    return psycopg.connect(url, connect_timeout=6, autocommit=False)


def ensure_schema() -> None:
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asi_worker_lease (
                    singleton SMALLINT PRIMARY KEY CHECK (singleton=1),
                    holder_id TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    worker_generation TEXT,
                    runtime_build TEXT,
                    acquired_at TIMESTAMPTZ NOT NULL,
                    heartbeat_at TIMESTAMPTZ NOT NULL,
                    lease_until TIMESTAMPTZ NOT NULL,
                    lease_version TEXT NOT NULL
                )
            """)
        con.commit()
    finally:
        con.close()


def _identity(runtime_identity=None) -> dict:
    identity = dict(runtime_identity or {})
    device = detect_device_type()
    generation = str(identity.get("generation") or "unknown")
    host = socket.gethostname().strip() or "device"
    holder = f"{device}:{host}:{generation}"
    return {
        "holder_id": holder,
        "device_type": device,
        "priority": device_priority(device),
        "generation": generation,
        "build": str(identity.get("build") or "unknown"),
        "pid": int(identity.get("pid") or os.getpid()),
    }


def try_claim(runtime_identity=None) -> dict:
    """Atomically acquire an expired/empty lease, or renew our own lease."""
    ident = _identity(runtime_identity)
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO asi_worker_lease(
                    singleton,holder_id,device_type,priority,worker_generation,
                    runtime_build,acquired_at,heartbeat_at,lease_until,lease_version
                ) VALUES(
                    1,%s,%s,%s,%s,%s,now(),now(),
                    now() + (%s * interval '1 second'),%s
                )
                ON CONFLICT(singleton) DO UPDATE SET
                    holder_id=excluded.holder_id,
                    device_type=excluded.device_type,
                    priority=excluded.priority,
                    worker_generation=excluded.worker_generation,
                    runtime_build=excluded.runtime_build,
                    acquired_at=CASE
                        WHEN asi_worker_lease.holder_id=excluded.holder_id
                        THEN asi_worker_lease.acquired_at ELSE now() END,
                    heartbeat_at=now(),
                    lease_until=now() + (%s * interval '1 second'),
                    lease_version=excluded.lease_version
                WHERE asi_worker_lease.lease_until <= now()
                   OR asi_worker_lease.holder_id=excluded.holder_id
                RETURNING holder_id,device_type,priority,heartbeat_at,lease_until
                """,
                (
                    ident["holder_id"], ident["device_type"], ident["priority"],
                    ident["generation"], ident["build"], LEASE_TTL_SECONDS,
                    LEASE_VERSION, LEASE_TTL_SECONDS,
                ),
            )
            row = cur.fetchone()
            if row:
                con.commit()
                return {
                    "acquired": True,
                    "holder_id": str(row[0]),
                    "device_type": str(row[1]),
                    "priority": int(row[2]),
                    "heartbeat_at": row[3],
                    "lease_until": row[4],
                }
            cur.execute(
                "SELECT device_type,priority,heartbeat_at,lease_until "
                "FROM asi_worker_lease WHERE singleton=1"
            )
            owner = cur.fetchone()
        con.rollback()
        return {
            "acquired": False,
            "owner_device": "" if not owner else str(owner[0]),
            "owner_priority": 0 if not owner else int(owner[1]),
            "owner_heartbeat_at": None if not owner else owner[2],
            "owner_lease_until": None if not owner else owner[3],
        }
    finally:
        con.close()


def acquire_or_wait(runtime_identity=None) -> dict:
    """Wait in standby until this device owns the single Neon lease."""
    if not enabled():
        return {"acquired": False, "status": "HYBRID_DISABLED"}
    ensure_schema()
    ident = _identity(runtime_identity)
    delay = startup_delay_seconds(ident["device_type"])
    if delay:
        print(
            f"[HYBRID_LEASE] STARTUP_BIAS device={ident['device_type']} delay={delay:.1f}s",
            flush=True,
        )
        time.sleep(delay)
    last_report = 0.0
    while True:
        try:
            result = try_claim(runtime_identity)
        except SystemExit:
            raise
        except Exception as exc:
            now = time.monotonic()
            if now - last_report >= 60:
                print(
                    f"[HYBRID_LEASE] DATABASE_RETRY error_type={type(exc).__name__}",
                    flush=True,
                )
                last_report = now
            time.sleep(POLL_SECONDS)
            continue
        if result.get("acquired"):
            print(
                f"[HYBRID_LEASE] PRIMARY device={ident['device_type']} "
                f"priority={ident['priority']} ttl={LEASE_TTL_SECONDS}s",
                flush=True,
            )
            global _ACTIVE_IDENTITY, _LAST_RENEW_MONOTONIC
            _ACTIVE_IDENTITY = ident
            _LAST_RENEW_MONOTONIC = time.monotonic()
            return result
        now = time.monotonic()
        if now - last_report >= 60:
            print(
                f"[HYBRID_LEASE] STANDBY device={ident['device_type']} "
                f"active={result.get('owner_device') or 'unknown'}",
                flush=True,
            )
            last_report = now
        time.sleep(POLL_SECONDS)


def renew(runtime_identity=None) -> bool:
    ident = _identity(runtime_identity or _ACTIVE_IDENTITY)
    con = _pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE asi_worker_lease SET
                    heartbeat_at=now(),
                    lease_until=now() + (%s * interval '1 second'),
                    runtime_build=%s,
                    lease_version=%s
                WHERE singleton=1 AND holder_id=%s
                RETURNING lease_until
                """,
                (LEASE_TTL_SECONDS, ident["build"], LEASE_VERSION, ident["holder_id"]),
            )
            row = cur.fetchone()
        con.commit()
        return bool(row)
    finally:
        con.close()


def release(runtime_identity=None) -> bool:
    # Cleanup is only meaningful after this process actually became PRIMARY.
    # Importing the module in CI/UI must never trigger a database connection.
    if runtime_identity is None and _ACTIVE_IDENTITY is None:
        return False
    if not enabled():
        return False
    url = database_url()
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        return False
    ident = _identity(runtime_identity or _ACTIVE_IDENTITY)
    try:
        con = _pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute(
                    """
                    UPDATE asi_worker_lease SET
                        heartbeat_at=now(), lease_until=now()
                    WHERE singleton=1 AND holder_id=%s
                    """,
                    (ident["holder_id"],),
                )
            con.commit()
            return True
        finally:
            con.close()
    except BaseException:
        return False


def _renew_loop(runtime_identity):
    global _LAST_RENEW_MONOTONIC
    while not _STOP.wait(RENEW_SECONDS):
        try:
            if not renew(runtime_identity):
                print("[HYBRID_LEASE] LEASE_LOST fail-closed", flush=True)
                os._exit(76)
            _LAST_RENEW_MONOTONIC = time.monotonic()
        except Exception as exc:
            age = time.monotonic() - float(_LAST_RENEW_MONOTONIC or 0.0)
            print(
                f"[HYBRID_LEASE] RENEW_RETRY error_type={type(exc).__name__} age={age:.0f}s",
                flush=True,
            )
            if _LAST_RENEW_MONOTONIC is not None and age >= LEASE_TTL_SECONDS:
                print("[HYBRID_LEASE] RENEW_EXPIRED fail-closed", flush=True)
                os._exit(75)


def start_renewal(runtime_identity=None):
    global _RENEW_THREAD
    if not enabled() or _RENEW_THREAD is not None:
        return _RENEW_THREAD
    _STOP.clear()
    _RENEW_THREAD = threading.Thread(
        target=_renew_loop,
        args=(dict(runtime_identity or {}),),
        name="asi-hybrid-neon-lease",
        daemon=True,
    )
    _RENEW_THREAD.start()
    return _RENEW_THREAD


def stop_and_release(runtime_identity=None):
    _STOP.set()
    if runtime_identity is None and _ACTIVE_IDENTITY is None:
        return False
    return release(runtime_identity)


atexit.register(stop_and_release)
