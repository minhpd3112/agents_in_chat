#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY_BIN="$SCRIPT_DIR/cli-proxy-api"
if [ ! -f "$PROXY_BIN" ]; then PROXY_BIN="$SCRIPT_DIR/cli-proxy-api.exe"; fi

if pgrep -f "cli-proxy-api" > /dev/null; then
    echo "-> CLIProxyAPI dang chay."
else
    cd "$SCRIPT_DIR"
    nohup "$PROXY_BIN" > /dev/null 2>&1 &
    sleep 1
    echo "-> [ONLINE] CLIProxyAPI da khoi dong tai port 8080."
fi
