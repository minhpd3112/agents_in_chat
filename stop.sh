#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python (python3 or python) not found in PATH." >&2
    exit 1
fi
exec "$PYTHON_BIN" -B "$SCRIPT_DIR/scripts/proxy_manager.py" stop
