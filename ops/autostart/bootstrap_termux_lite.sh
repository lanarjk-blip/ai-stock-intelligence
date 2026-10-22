#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "[TERMUX_LITE] Updating Termux packages..."
pkg update -y

echo "[TERMUX_LITE] Installing prebuilt native Python packages..."
pkg install -y python git python-numpy python-pandas python-pyarrow

echo "[TERMUX_LITE] Installing pure/light Python dependencies..."
python -m pip install -r requirements-termux.txt

echo "[TERMUX_LITE] Verifying runtime imports..."
python - <<'PY'
import numpy, pandas, pyarrow, streamlit, requests, psycopg, fastapi, uvicorn
print("TERMUX_LITE_IMPORTS_OK")
print("numpy", numpy.__version__)
print("pandas", pandas.__version__)
print("pyarrow", pyarrow.__version__)
print("streamlit", streamlit.__version__)
PY

echo "[TERMUX_LITE] Ready. Start with: bash ops/autostart/start_termux_lite.sh"
