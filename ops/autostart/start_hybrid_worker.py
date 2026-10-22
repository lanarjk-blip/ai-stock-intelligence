"""Safe local bootstrap for AI Stock Intelligence hybrid worker.

This file deliberately does not contain DATABASE_URL. The secret stays on the
host in a local env file (outside the repository) or in the host environment.

Usage:
    python ops/autostart/start_hybrid_worker.py --env-file /path/to/asi-runtime.env

HP/Termux mode deliberately enables ASI_TERMUX_LITE so native-heavy optional
packages such as XGBoost are never required locally. The worker remains fail-open
and keeps the proven baseline/risk guards alive rather than compiling C++ on a
phone.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HYBRID = ROOT / "hybrid_worker.py"


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=os.getenv("ASI_ENV_FILE", ""))
    parser.add_argument("--device", choices=["auto", "laptop", "hp"], default="auto")
    args = parser.parse_args()

    if args.env_file:
        load_env_file(Path(args.env_file).expanduser())

    database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not database_url:
        print("AUTOSTART BLOCKED: DATABASE_URL/NEON_DATABASE_URL is not available.", flush=True)
        return 2
    if not HYBRID.exists():
        print(f"AUTOSTART BLOCKED: missing {HYBRID}", flush=True)
        return 3

    env = os.environ.copy()
    if args.device != "auto":
        env["ASI_DEVICE_TYPE"] = args.device
    if args.device == "hp" or env.get("ASI_DEVICE_TYPE") == "hp":
        env["ASI_TERMUX_LITE"] = "1"
        env.setdefault("AI_ENGINE_WORKER", "1")

    mode = " TERMUX_LITE" if env.get("ASI_TERMUX_LITE") == "1" else ""
    print(f"AUTOSTART READY:{mode} launching hybrid_worker.py --daemon (credential hidden).", flush=True)
    proc = subprocess.run(
        [sys.executable, str(HYBRID), "--daemon"],
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
