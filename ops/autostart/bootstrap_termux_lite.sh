#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "[TERMUX_LITE] Updating Termux packages..."
pkg update -y

echo "[TERMUX_LITE] Installing prebuilt native Python packages only..."
pkg install -y python git python-numpy python-pandas python-pyarrow

echo "[TERMUX_LITE] Native packages ready. Pip dependency resolver is intentionally disabled on Android."
echo "[TERMUX_LITE] Verifying required runtime imports..."
python - <<'PY'
import importlib
mods = ["numpy", "pandas", "pyarrow", "streamlit", "requests", "psycopg", "fastapi", "uvicorn"]
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append((name, str(exc)))
if missing:
    print("TERMUX_LITE_MISSING_IMPORTS")
    for name, err in missing:
        print(f"MISSING {name}: {err}")
    raise SystemExit(3)
import numpy, pandas, pyarrow, streamlit
print("TERMUX_LITE_IMPORTS_OK")
print("numpy", numpy.__version__)
print("pandas", pandas.__version__)
print("pyarrow", pyarrow.__version__)
print("streamlit", streamlit.__version__)
PY

echo "[TERMUX_LITE] Ready. Start with: bash ops/autostart/start_termux_lite.sh"
