#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if pkill -f "cli-proxy-api"; then
    echo "-> [OFFLINE] Da tat tien trinh CLIProxyAPI."
else
    echo "-> CLIProxyAPI hien khong chay."
fi

# [SAFETY] Auto-Backup: snapshot trang thai token moi nhat sau khi proxy da dung han.
PYTHON_BIN="$(command -v python3 || command -v python)"
if [ -f "$SCRIPT_DIR/scripts/backup_auths.py" ]; then
    "$PYTHON_BIN" -B "$SCRIPT_DIR/scripts/backup_auths.py" backup || true
fi
