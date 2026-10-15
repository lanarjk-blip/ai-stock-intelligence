"""Market-day launcher for Laptop + HP hybrid workers.

Run this script instead of worker_base.py on local devices.
It keeps the engine active only during the configured WIB market-day window,
while runtime_supervisor + hybrid_worker_lease decide which device is PRIMARY.

Examples:
  python hybrid_worker.py --device laptop --daemon
  python hybrid_worker.py --device hp --daemon

`--daemon` sleeps outside the window and starts again on the next trading day.
Without it, the launcher exits when no active market-day window exists or after
the current window closes.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
WIB = timezone(timedelta(hours=7))
START_TIME = dtime(7, 45)
END_TIME = dtime(16, 30)


def _calendar_holidays() -> set[str]:
    path = BASE / "data" / "idx_holidays.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        holidays = payload.get("holidays") or {}
        out = set()
        for values in holidays.values():
            out.update(str(v) for v in (values or []))
        return out
    except Exception:
        # Fallback remains weekday-only rather than refusing to start. The
        # engine's market_session module still supplies the authoritative guard.
        return set()


def is_trading_day(day: date, holidays=None) -> bool:
    h = _calendar_holidays() if holidays is None else set(holidays)
    return day.weekday() < 5 and day.isoformat() not in h


def market_window(now=None):
    current = now or datetime.now(WIB)
    if current.tzinfo is None:
        current = current.replace(tzinfo=WIB)
    else:
        current = current.astimezone(WIB)
    day = current.date()
    start = datetime.combine(day, START_TIME, tzinfo=WIB)
    end = datetime.combine(day, END_TIME, tzinfo=WIB)
    active = is_trading_day(day) and start <= current < end
    return {"now": current, "day": day, "start": start, "end": end, "active": active}


def next_start(now=None):
    current = now or datetime.now(WIB)
    if current.tzinfo is None:
        current = current.replace(tzinfo=WIB)
    else:
        current = current.astimezone(WIB)
    for offset in range(0, 370):
        day = current.date() + timedelta(days=offset)
        if not is_trading_day(day):
            continue
        candidate = datetime.combine(day, START_TIME, tzinfo=WIB)
        if candidate > current:
            return candidate
    raise RuntimeError("No future IDX trading day found in scheduling horizon")


def detect_device(env=None) -> str:
    e = os.environ if env is None else env
    explicit = str(e.get("ASI_WORKER_DEVICE") or "").strip().lower()
    if explicit in {"laptop", "hp", "render"}:
        return explicit
    if e.get("RENDER") or e.get("RENDER_SERVICE_ID"):
        return "render"
    prefix = str(e.get("PREFIX") or "").lower()
    if e.get("ANDROID_ROOT") or "com.termux" in prefix or "/data/data/com.termux" in prefix:
        return "hp"
    return "laptop"


def child_environment(device: str, base_env=None) -> dict:
    env = dict(os.environ if base_env is None else base_env)
    env["AI_ENGINE_WORKER"] = "1"
    env["ASI_HYBRID_WORKER"] = "1"
    env["ASI_WORKER_DEVICE"] = device
    env["ASI_DEVICE_PROFILE"] = "HP_FAST" if device == "hp" else "FULL"
    # Keep numerical libraries from spawning a desktop-sized thread farm on a
    # phone. Network collection remains controlled by the engine itself.
    if device == "hp":
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("OPENBLAS_NUM_THREADS", "2")
        env.setdefault("MKL_NUM_THREADS", "2")
        env.setdefault("NUMEXPR_NUM_THREADS", "2")
        env.setdefault("ASI_HYBRID_CLAIM_DELAY_SECONDS", "8")
    elif device == "laptop":
        env.setdefault("ASI_HYBRID_CLAIM_DELAY_SECONDS", "2")
    else:
        env.setdefault("ASI_HYBRID_CLAIM_DELAY_SECONDS", "0")
    return env


def _database_configured(env: dict) -> bool:
    url = str(env.get("DATABASE_URL") or env.get("NEON_DATABASE_URL") or "")
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _terminate_child(proc: subprocess.Popen, timeout=20):
    if proc.poll() is not None:
        return proc.returncode
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
        return proc.returncode
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            return proc.wait(timeout=5)
        except Exception:
            return None


def run_window(device: str) -> int:
    env = child_environment(device)
    if not _database_configured(env):
        print(
            "[HYBRID_LAUNCHER] BLOCKED: set DATABASE_URL or NEON_DATABASE_URL first.",
            flush=True,
        )
        return 2

    worker = BASE / "worker_base.py"
    if not worker.exists():
        print("[HYBRID_LAUNCHER] BLOCKED: worker_base.py not found", flush=True)
        return 3

    print(
        f"[HYBRID_LAUNCHER] START device={device} profile={env['ASI_DEVICE_PROFILE']} "
        f"window={START_TIME.strftime('%H:%M')}-{END_TIME.strftime('%H:%M')} WIB",
        flush=True,
    )

    while True:
        status = market_window()
        if not status["active"]:
            print("[HYBRID_LAUNCHER] MARKET_WINDOW_CLOSED", flush=True)
            return 0
        proc = subprocess.Popen(
            [sys.executable, str(worker)],
            cwd=str(BASE),
            env=env,
        )
        while proc.poll() is None:
            if not market_window()["active"]:
                _terminate_child(proc)
                print("[HYBRID_LAUNCHER] SESSION_COMPLETE", flush=True)
                return 0
            time.sleep(15)
        code = int(proc.returncode or 0)
        if not market_window()["active"]:
            return code
        # Fail-closed lease exits (75/76), transient crashes and ordinary exits
        # are restarted during the active window. Neon decides whether the new
        # process is PRIMARY or STANDBY.
        print(f"[HYBRID_LAUNCHER] WORKER_EXIT code={code}; restart in 15s", flush=True)
        time.sleep(15)


def daemon_loop(device: str) -> int:
    while True:
        status = market_window()
        if status["active"]:
            run_window(device)
            continue
        target = next_start(status["now"])
        wait = max(5.0, (target - datetime.now(WIB)).total_seconds())
        print(
            f"[HYBRID_LAUNCHER] SLEEP next_start={target.isoformat()} device={device}",
            flush=True,
        )
        # Wake periodically so clock/timezone changes do not strand the daemon.
        time.sleep(min(wait, 300.0))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI Stock Intelligence hybrid market-day worker")
    parser.add_argument("--device", choices=("laptop", "hp", "render"), default=None)
    parser.add_argument("--daemon", action="store_true", help="sleep and resume on future trading days")
    args = parser.parse_args(argv)
    device = args.device or detect_device()
    if args.daemon:
        return daemon_loop(device)
    if not market_window()["active"]:
        target = next_start()
        print(
            f"[HYBRID_LAUNCHER] NOT_ACTIVE next_start={target.isoformat()} "
            "(use --daemon to wait automatically)",
            flush=True,
        )
        return 0
    return run_window(device)


if __name__ == "__main__":
    raise SystemExit(main())
