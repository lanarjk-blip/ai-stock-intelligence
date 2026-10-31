#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export ASI_DEVICE_TYPE="hp"
export ASI_TERMUX_LITE="1"
export ASI_WORKER_DEVICE="hp"
export AI_ENGINE_WORKER="1"

# Make project root visible during Python startup so sitecustomize.py is loaded.
# That bootstrap installs runtime supervisor, Neon lease, persistent bridge,
# heartbeat and checkpoint persistence before worker_base runs. config.json now
# selects the focused Time Map workload; legacy schedulers do not start.
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$ROOT:$PYTHONPATH"
else
  export PYTHONPATH="$ROOT"
fi

# Give the runtime heartbeat a source-aware deploy suffix even on Termux.
if command -v git >/dev/null 2>&1; then
  export GITHUB_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || true)"
fi

# Retire stale direct workers/launchers before starting one authoritative HP launcher.
pkill -f "python .*worker_base.py" 2>/dev/null || true
pkill -f "python .*hybrid_worker.py.*--device hp" 2>/dev/null || true

# If started before the IDX worker window, the daemon sleeps and wakes automatically.
# During the active window it starts worker_base through the hybrid path, which is the
# only supported path for Neon lease + persistent bridge synchronization.
echo "[TERMUX_LITE] Starting supervised Time Map worker (legacy scans disabled by config)."
exec python hybrid_worker.py --device hp --daemon
