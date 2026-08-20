#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  agents_in_chat: One-Click Installer for Linux / macOS / WSL
#  Tu dong cau hinh Codex CLI, dang ky lenh toan cuc 'aic' & Khoi dong Proxy
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== [1/6] Kiem tra moi truong agents_in_chat ==="

PROXY_BIN="$SCRIPT_DIR/cli-proxy-api"
if [ ! -f "$PROXY_BIN" ] && [ -f "$SCRIPT_DIR/cli-proxy-api.exe" ]; then
    PROXY_BIN="$SCRIPT_DIR/cli-proxy-api.exe"
fi

if [ ! -f "$PROXY_BIN" ]; then
    echo "-> Khong tim thay binary cli-proxy-api, dang tai tu GitHub Releases..."
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    if [ "$ARCH" = "x86_64" ]; then ARCH="x86_64"; elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then ARCH="arm64"; fi
    DOWNLOAD_URL="https://github.com/router-for-me/CLIProxyAPI/releases/latest/download/CLIProxyAPI_${OS}_${ARCH}.tar.gz"
    if curl -fsSL "$DOWNLOAD_URL" -o "$SCRIPT_DIR/cliproxy.tar.gz" 2>/dev/null; then
        tar -xzf "$SCRIPT_DIR/cliproxy.tar.gz" -C "$SCRIPT_DIR"
        rm -f "$SCRIPT_DIR/cliproxy.tar.gz"
        chmod +x "$PROXY_BIN" 2>/dev/null || true
        echo "-> Da tai va giai nen cli-proxy-api thanh cong!"
    else
        echo "[warn] Khong the tu dong tai binary. Vui long tai thu cong tu: https://github.com/router-for-me/CLIProxyAPI/releases"
    fi
fi


AUTHS_DIR="$SCRIPT_DIR/auths"
mkdir -p "$AUTHS_DIR"
AUTH_COUNT=$(find "$AUTHS_DIR" -name "*.json" 2>/dev/null | wc -l || echo 0)
echo "-> Phat hien $AUTH_COUNT tai khoan OAuth trong auths/"

echo "=== [2/6] Cau hinh ~/.codex/config.toml ==="
python3 "$SCRIPT_DIR/scripts/configure_codex_toml.py" custom 2>/dev/null || python "$SCRIPT_DIR/scripts/configure_codex_toml.py" custom

echo "=== [3/6] Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ==="
CODEX_DIR="$HOME/.codex"
MODELS_CACHE="$CODEX_DIR/models_cache.json"
TEMPLATE_JSON="$SCRIPT_DIR/docs/models_cache_template.json"

chmod 644 "$MODELS_CACHE" 2>/dev/null || true
cp -f "$TEMPLATE_JSON" "$MODELS_CACHE"
chmod 444 "$MODELS_CACHE"
echo "-> Da nap 6 models & KHOA READ-ONLY thanh cong vao models_cache.json."

echo "=== [4/6] Dong bo lich su chat sang provider 'custom' ==="
python3 "$SCRIPT_DIR/scripts/sync_sessions.py" custom 2>/dev/null || python "$SCRIPT_DIR/scripts/sync_sessions.py" custom

echo "=== [5/6] Dang ky lenh toan cuc 'aic' vao PATH ==="
mkdir -p "$HOME/.local/bin"
chmod +x "$SCRIPT_DIR/bin/aic"
ln -sf "$SCRIPT_DIR/bin/aic" "$HOME/.local/bin/aic"
echo "-> Da tao symlink toan cuc 'aic' tai $HOME/.local/bin/aic"

echo "=== [6/6] Khoi dong CLIProxyAPI ==="
"$SCRIPT_DIR/stop.sh" > /dev/null 2>&1 || true
"$SCRIPT_DIR/start.sh"

echo "============================================================"
echo "   CAI DAT & DANG KY LENH TOAN CUC 'aic' THANH CONG 100%!"
echo "============================================================"
echo "Bay gio ban co the mo Terminal tai BAT KY THU MUC NAO va dung:"
echo "  - Bat dau chat:      codex"
echo "  - Kiem tra he thong: aic status"
echo "  - Chay kiem thu:     aic test"
echo "  - Tat / Bat proxy:   aic stop  /  aic start"
echo "  - Khoi phuc goc:     aic uninstall"
