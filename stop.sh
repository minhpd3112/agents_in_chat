#!/usr/bin/env bash
if pkill -f "cli-proxy-api"; then
    echo "-> [OFFLINE] Da tat tien trinh CLIProxyAPI."
else
    echo "-> CLIProxyAPI hien khong chay."
fi
