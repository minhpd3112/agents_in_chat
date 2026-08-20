#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY_BIN="$SCRIPT_DIR/cli-proxy-api"
if [ ! -f "$PROXY_BIN" ]; then PROXY_BIN="$SCRIPT_DIR/cli-proxy-api.exe"; fi

if pgrep -f "cli-proxy-api" > /dev/null; then
    echo "-> [ONLINE] CLIProxyAPI dang hoat dong san sang."
    exit 0
else
    cd "$SCRIPT_DIR"
    nohup "$PROXY_BIN" -config "$SCRIPT_DIR/config.yaml" > /dev/null 2>&1 &
    sleep 2
    if pgrep -f "cli-proxy-api" > /dev/null; then
        echo "-> [ONLINE] CLIProxyAPI da khoi dong chay ngam thanh cong."
        exit 0
    else
        echo "-> [WARNING] Da chay binary nhung dich vu proxy chua phan hoi."
        exit 1
    fi
fi
