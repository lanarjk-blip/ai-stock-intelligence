#!/data/data/com.termux/files/usr/bin/bash
# Install locally as ~/.termux/boot/start-ai-stock.sh when using Termux:Boot.
# Keep the env file outside git and chmod 600 it.
set -u

REPO_DIR="${ASI_REPO_DIR:-$HOME/ai-stock-intelligence}"
ENV_FILE="${ASI_ENV_FILE:-$HOME/.config/ai-stock-intelligence/runtime.env}"
LOG_DIR="$HOME/.local/state/ai-stock-intelligence"
mkdir -p "$LOG_DIR"

cd "$REPO_DIR" || exit 20

# Give Android/network/storage a short boot grace period. Lease takeover itself is
# handled safely by hybrid_worker_lease.py; no manual lease reset is performed.
sleep 20

nohup python "$REPO_DIR/ops/autostart/start_hybrid_worker.py" \
  --device hp \
  --env-file "$ENV_FILE" \
  >> "$LOG_DIR/autostart.log" 2>&1 &
