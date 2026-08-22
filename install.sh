#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  agents_in_chat: One-Click Installer for Linux / macOS / WSL
#  Tu dong cau hinh Codex CLI, dang ky lenh toan cuc 'aic' & Khoi dong Proxy
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 0. Test Mode & Environment Isolation
AIC_TEST_MODE="${AIC_TEST_MODE:-0}"
CODEX_DIR="${AIC_CODEX_DIR:-$HOME/.codex}"
BIN_LINK_DIR="${AIC_BIN_LINK_DIR:-$HOME/.local/bin}"
AIC_SKIP_DOWNLOAD="${AIC_SKIP_DOWNLOAD:-0}"
AIC_SKIP_PROXY="${AIC_SKIP_PROXY:-0}"
AIC_FAIL_STEP="${AIC_FAIL_STEP:-}"

MODELS_CACHE="$CODEX_DIR/models_cache.json"
CONFIG_SCRIPT="$SCRIPT_DIR/scripts/configure_codex_toml.py"
SYNC_SCRIPT="$SCRIPT_DIR/scripts/sync_sessions.py"

# Preflight: Mandatory Helper validation
if [ ! -f "$CONFIG_SCRIPT" ]; then
    echo "[ERROR] Thieu helper bat buoc tai $CONFIG_SCRIPT"
    exit 1
fi
if [ ! -f "$SYNC_SCRIPT" ]; then
    echo "[ERROR] Thieu helper bat buoc tai $SYNC_SCRIPT"
    exit 1
fi

# 1. Tim kiem Python executable
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "[ERROR] Khong tim thay Python! Vui long cai dat Python (>=3.8) truoc khi chay install."
    exit 1
fi

STATE_SYMLINK_ADDED=0

rollback() {
    echo -e "\n[ROLLBACK] Phat hien su co, dang hoan tac toan dien he thong..."
    "$PYTHON_BIN" "$CONFIG_SCRIPT" restore >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" openai >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" --verify openai >/dev/null 2>&1 || true
    if [ -f "$MODELS_CACHE" ]; then
        chmod 644 "$MODELS_CACHE" 2>/dev/null || true
        rm -f "$MODELS_CACHE"
    fi
    if [ "$STATE_SYMLINK_ADDED" -eq 1 ] && [ -L "$BIN_LINK_DIR/aic" ]; then
        rm -f "$BIN_LINK_DIR/aic"
    fi
    echo "-> Da hoan tac an toan. Vui long kiem tra loi tren va chay lai install.sh."
    exit 1
}

echo "=== [1/6] Kiem tra moi truong agents_in_chat ==="

PROXY_BIN="$SCRIPT_DIR/cli-proxy-api"
if [ ! -f "$PROXY_BIN" ] && [ -f "$SCRIPT_DIR/cli-proxy-api.exe" ]; then
    PROXY_BIN="$SCRIPT_DIR/cli-proxy-api.exe"
fi

if [ ! -f "$PROXY_BIN" ] && [ "$AIC_SKIP_DOWNLOAD" -ne 1 ]; then
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

if [ ! -f "$SCRIPT_DIR/config.yaml" ] && [ -f "$SCRIPT_DIR/config.example.yaml" ]; then
    cp "$SCRIPT_DIR/config.example.yaml" "$SCRIPT_DIR/config.yaml"
    echo "-> Da khoi tao config.yaml tu config.example.yaml."
fi

AUTHS_DIR="$SCRIPT_DIR/auths"
mkdir -p "$AUTHS_DIR"

echo "=== [2/6] Backup & Cau hinh ~/.codex/config.toml ==="
"$PYTHON_BIN" "$CONFIG_SCRIPT" custom || rollback

echo "=== [3/6] Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ==="
TEMPLATE_JSON="$SCRIPT_DIR/docs/models_cache_template.json"
if [ ! -f "$TEMPLATE_JSON" ]; then
    echo "[ERROR] Khong tim thay template tai $TEMPLATE_JSON!"
    rollback
fi

mkdir -p "$CODEX_DIR"
chmod 644 "$MODELS_CACHE" 2>/dev/null || true
"$PYTHON_BIN" -c "
import json, subprocess, re
template_path = '$TEMPLATE_JSON'
cache_path = '$MODELS_CACHE'
ver = '0.149.0'
try:
    p = subprocess.run(['codex', '--version'], capture_output=True, text=True)
    m = re.search(r'(\d+\.\d+\.\d+)', p.stdout)
    if m: ver = m.group(1)
except Exception: pass
with open(template_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
data['client_version'] = ver
with open(cache_path, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || cp -f "$TEMPLATE_JSON" "$MODELS_CACHE"
chmod 444 "$MODELS_CACHE"

AUTHS_DIR="$SCRIPT_DIR/auths"
mkdir -p "$AUTHS_DIR"
ZEN_AUTH="$AUTHS_DIR/openai-compatible-opencode-zen.json"
if [ ! -f "$ZEN_AUTH" ]; then
    cat << 'EOF' > "$ZEN_AUTH"
{
  "type": "openai-compatible",
  "provider": "openai-compatible-opencode-zen",
  "name": "opencode-zen",
  "url": "https://opencode.ai/zen/v1",
  "base_url": "https://opencode.ai/zen/v1",
  "key": "public",
  "api_key": "public",
  "models": [
    "x-preview-f-free",
    "ox-alpha"
  ]
}
EOF
fi

MODEL_COUNT=$("$PYTHON_BIN" -c "import json; print(len(json.load(open('$TEMPLATE_JSON', encoding='utf-8')).get('models', [])))" 2>/dev/null || echo "")
if [ -n "$MODEL_COUNT" ] && [ "$MODEL_COUNT" -gt 0 ] 2>/dev/null; then
    echo "-> Da nap $MODEL_COUNT dinh nghia model & KHOA READ-ONLY cache menu cho Codex CLI."
else
    echo "-> Da nap danh muc model & KHOA READ-ONLY cache menu cho Codex CLI."
fi

echo "=== [4/6] Dong bo & Xac minh lich su chat sang provider 'custom' ==="
"$PYTHON_BIN" "$SYNC_SCRIPT" custom || rollback
"$PYTHON_BIN" "$SYNC_SCRIPT" --verify custom || rollback

echo "=== [5/6] Dang ky lenh toan cuc 'aic' vao PATH ==="
mkdir -p "$BIN_LINK_DIR"
chmod +x "$SCRIPT_DIR/bin/aic"
if [ -e "$BIN_LINK_DIR/aic" ] && [ ! -L "$BIN_LINK_DIR/aic" ]; then
    echo "[WARN] $BIN_LINK_DIR/aic da ton tai truoc do. Khong ghi de am tham."
else
    ln -sf "$SCRIPT_DIR/bin/aic" "$BIN_LINK_DIR/aic"
    STATE_SYMLINK_ADDED=1
    echo "-> Da tao symlink toan cuc 'aic' tai $BIN_LINK_DIR/aic"
fi

# Register alias in ~/.bashrc or ~/.zshrc
PROFILE_FILE=""
if [ -n "$AIC_PROFILE_PATH" ]; then
    PROFILE_FILE="$AIC_PROFILE_PATH"
elif [ -f "$HOME/.zshrc" ]; then
    PROFILE_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PROFILE_FILE="$HOME/.bashrc"
fi

if [ -n "$PROFILE_FILE" ]; then
    if ! grep -q "alias aic=" "$PROFILE_FILE" 2>/dev/null; then
        echo "" >> "$PROFILE_FILE"
        echo "# >>> AIC >>>" >> "$PROFILE_FILE"
        echo "alias aic=\"$PYTHON_BIN $SCRIPT_DIR/bin/aic.py\"" >> "$PROFILE_FILE"
        echo "# <<< AIC <<<" >> "$PROFILE_FILE"
        echo "-> Da dang ky alias 'aic' vao $PROFILE_FILE"
    fi
fi

echo "=== [6/6] Khoi dong CLIProxyAPI ==="
if [ "$AIC_FAIL_STEP" = "start" ]; then
    echo "[FAIL_INJECTION] Injected failure at start"
    rollback
fi

if [ "$AIC_SKIP_PROXY" -ne 1 ]; then
    "$SCRIPT_DIR/stop.sh" > /dev/null 2>&1 || true
    "$SCRIPT_DIR/start.sh"
else
    echo "-> [TEST_MODE] Bo qua khoi dong proxy."
fi

echo ""
echo "============================================================"
echo "   CAI DAT & DANG KY LENH TOAN CUC 'aic' THANH CONG 100%!"
echo "============================================================"
echo "Cac buoc tiep theo:"
echo "  1. Nap tai khoan vao pool (Neu chua co):"
echo "     - Google Antigravity: aic login_agy"
echo "     - OpenAI Codex:       aic login_codex"
echo "     - Ox Alpha:           San sang su dung ngay (Mien phi, khong can login)"
echo ""
echo "  2. Bat dau su dung:"
echo "     - Khoi chay Codex:    codex"
echo "     - Kiem tra he thong:  aic status"
echo "     - Chay kiem thu:      aic test"
echo "     - Tat / Bat proxy:    aic stop  /  aic start"
echo "     - Khoi phuc goc:      aic uninstall"
echo ""
