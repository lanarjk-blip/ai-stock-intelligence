#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export ASI_DEVICE_TYPE="hp"
export ASI_TERMUX_LITE="1"
export AI_ENGINE_WORKER="1"

# Kill only stale direct worker instances. Hybrid launcher/schedulers can be
# restarted separately if the user uses them.
pkill -f "python .*worker_base.py" 2>/dev/null || true

echo "[TERMUX_LITE] Starting worker_base.py (XGBoost disabled locally; fail-open ensemble)."
exec python worker_base.py
