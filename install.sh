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

if [ "$AIC_TEST_MODE" = "1" ] && [ -n "${AIC_CONFIG_SCRIPT:-}" ]; then
    CONFIG_SCRIPT="$AIC_CONFIG_SCRIPT"
else
    CONFIG_SCRIPT="$SCRIPT_DIR/scripts/configure_codex_toml.py"
fi

if [ "$AIC_TEST_MODE" = "1" ] && [ -n "${AIC_SYNC_SCRIPT:-}" ]; then
    SYNC_SCRIPT="$AIC_SYNC_SCRIPT"
else
    SYNC_SCRIPT="$SCRIPT_DIR/scripts/sync_sessions.py"
fi

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

if [ "$AIC_TEST_MODE" = "1" ] && [ -n "${AIC_CHECK_CODEX_SCRIPT:-}" ]; then
    CHECK_CODEX_SCRIPT="$AIC_CHECK_CODEX_SCRIPT"
else
    CHECK_CODEX_SCRIPT="$SCRIPT_DIR/scripts/check_codex_running.py"
fi
if [ ! -f "$CHECK_CODEX_SCRIPT" ]; then
    echo "[ERROR] Thieu helper bat buoc tai $CHECK_CODEX_SCRIPT"
    exit 1
fi

# Preflight: Check active Codex CLI process (fail-closed on 1 and 2)
"$PYTHON_BIN" -B "$CHECK_CODEX_SCRIPT" || exit $?

# Preflight: Validate auths isolation in test mode
if [ "$AIC_TEST_MODE" = "1" ]; then
    if [ -z "${AIC_AUTHS_DIR:-}" ] || [ -z "${AIC_AUTHS_BACKUP_DIR:-}" ]; then
        echo "[ERROR] AIC_TEST_MODE=1 requires AIC_AUTHS_DIR and AIC_AUTHS_BACKUP_DIR to be set" >&2
        exit 1
    fi
    AUTHS_DIR="$AIC_AUTHS_DIR"
    BACKUP_DIR="$AIC_AUTHS_BACKUP_DIR"
else
    AUTHS_DIR="$SCRIPT_DIR/auths"
    BACKUP_DIR="$SCRIPT_DIR/auths_backup"
fi


STATE_SYMLINK_ADDED=0
STATE_PROFILE_ADDED=0
PROFILE_FILE=""
PROFILE_STATE_FILE="$CODEX_DIR/aic_profile_rollback.json"

rollback() {
    echo -e "\n[ROLLBACK] Phat hien su co, dang hoan tac toan dien he thong..."
    "$PYTHON_BIN" "$CONFIG_SCRIPT" restore >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" openai >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" --verify openai >/dev/null 2>&1 || true
    if [ -f "$MODELS_CACHE" ]; then
        chmod 644 "$MODELS_CACHE" 2>/dev/null || true
        rm -f "$MODELS_CACHE"
    fi
    if [ "$STATE_PROFILE_ADDED" -eq 1 ] && [ -n "$PROFILE_FILE" ]; then
        "$PYTHON_BIN" "$SCRIPT_DIR/scripts/manage_profile.py" --profile "$PROFILE_FILE" --action rollback --state-file "$PROFILE_STATE_FILE" >/dev/null 2>&1 || true
    fi
    if [ "$STATE_SYMLINK_ADDED" -eq 1 ] && [ -L "$BIN_LINK_DIR/aic" ]; then
        rm -f "$BIN_LINK_DIR/aic"
    fi
    echo "-> Da hoan tac an toan. Vui long kiem tra loi tren va chay lai install.sh."
    exit 1
}

AIC_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || true)"
if [ -n "$AIC_VERSION" ]; then
    echo "=== Kiem tra moi truong agents_in_chat v$AIC_VERSION ==="
else
    echo "=== Kiem tra moi truong agents_in_chat ==="
fi

PROXY_BIN="$SCRIPT_DIR/cli-proxy-api"
if [ ! -f "$PROXY_BIN" ] && [ -f "$SCRIPT_DIR/cli-proxy-api.exe" ]; then
    PROXY_BIN="$SCRIPT_DIR/cli-proxy-api.exe"
fi

if [ "$AIC_TEST_MODE" != "1" ] && [ ! -f "$PROXY_BIN" ] && [ "$AIC_SKIP_DOWNLOAD" -ne 1 ]; then
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

if [ "$AIC_TEST_MODE" != "1" ] && [ ! -f "$SCRIPT_DIR/config.yaml" ] && [ -f "$SCRIPT_DIR/config.example.yaml" ]; then
    cp "$SCRIPT_DIR/config.example.yaml" "$SCRIPT_DIR/config.yaml"
    echo "-> Da khoi tao config.yaml tu config.example.yaml."
fi

mkdir -p "$AUTHS_DIR"

# [SAFETY] Khoi tao kho sao luu token & chup snapshot ban dau
if [ "$AIC_TEST_MODE" = "1" ] && [ -n "${AIC_BACKUP_SCRIPT:-}" ]; then
    BACKUP_SCRIPT="$AIC_BACKUP_SCRIPT"
else
    BACKUP_SCRIPT="$SCRIPT_DIR/scripts/backup_auths.py"
fi
if [ ! -f "$BACKUP_SCRIPT" ]; then
    echo "[ERROR] Thieu helper bat buoc tai $BACKUP_SCRIPT"
    exit 1
fi
echo "=== Khoi tao Atomic Auto-Backup cho thu muc auths/ ==="
mkdir -p "$BACKUP_DIR"
if ! "$PYTHON_BIN" -B "$BACKUP_SCRIPT" backup; then
    echo "[ERROR] Initial auth backup failed. Halting installation." >&2
    exit 1
fi

echo "=== Backup & Cau hinh ~/.codex/config.toml ==="
"$PYTHON_BIN" "$CONFIG_SCRIPT" custom || rollback

echo "=== Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ==="
TEMPLATE_JSON="$SCRIPT_DIR/docs/models_cache_template.json"
if [ ! -f "$TEMPLATE_JSON" ]; then
    echo "[ERROR] Khong tim thay template tai $TEMPLATE_JSON!"
    rollback
fi

mkdir -p "$CODEX_DIR"
chmod 644 "$MODELS_CACHE" 2>/dev/null || true
"$PYTHON_BIN" -c "
import sys, json, subprocess, re
template_path = sys.argv[1]
cache_path = sys.argv[2]
with open(template_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
ver = data.get('client_version', '0.153.0')
try:
    p = subprocess.run(['codex', '--version'], capture_output=True, text=True)
    m = re.search(r'(\d+\.\d+\.\d+)', p.stdout)
    if m: ver = m.group(1)
except Exception: pass
data['client_version'] = ver
with open(cache_path, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(data, f, indent=2)
" "$TEMPLATE_JSON" "$MODELS_CACHE" 2>/dev/null || cp -f "$TEMPLATE_JSON" "$MODELS_CACHE"
chmod 444 "$MODELS_CACHE"

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
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.3"
  ]
}
EOF
fi

MODEL_COUNT=$("$PYTHON_BIN" -c "import sys, json; print(len(json.load(open(sys.argv[1], encoding='utf-8')).get('models', [])))" "$TEMPLATE_JSON" 2>/dev/null || echo "")
if [ -n "$MODEL_COUNT" ] && [ "$MODEL_COUNT" -gt 0 ] 2>/dev/null; then
    echo "-> Da nap $MODEL_COUNT dinh nghia model & KHOA READ-ONLY cache menu cho Codex CLI."
else
    echo "-> Da nap danh muc model & KHOA READ-ONLY cache menu cho Codex CLI."
fi

echo "=== Dong bo & Xac minh lich su chat sang provider 'custom' ==="
"$PYTHON_BIN" "$SYNC_SCRIPT" custom || rollback
"$PYTHON_BIN" "$SYNC_SCRIPT" --verify custom || rollback

echo "=== Dang ky lenh toan cuc 'aic' vao PATH ==="
mkdir -p "$BIN_LINK_DIR"
chmod +x "$SCRIPT_DIR/bin/aic"
if [ -e "$BIN_LINK_DIR/aic" ] && [ ! -L "$BIN_LINK_DIR/aic" ]; then
    echo "[WARN] $BIN_LINK_DIR/aic da ton tai truoc do. Khong ghi de am tham."
else
    ln -sf "$SCRIPT_DIR/bin/aic" "$BIN_LINK_DIR/aic"
    STATE_SYMLINK_ADDED=1
    echo "-> Da tao symlink toan cuc 'aic' tai $BIN_LINK_DIR/aic"
fi

# Register Smart Wrapper & alias in ~/.bashrc or ~/.zshrc
PROFILE_FILE=""
if [ -n "${AIC_PROFILE_PATH:-}" ]; then
    PROFILE_FILE="$AIC_PROFILE_PATH"
elif [ -f "$HOME/.zshrc" ]; then
    PROFILE_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PROFILE_FILE="$HOME/.bashrc"
else
    PROFILE_FILE="$HOME/.bashrc"
fi

if [ -n "$PROFILE_FILE" ]; then
    SYNC_HELPER="$SCRIPT_DIR/scripts/sync_client_version.py"
    AIC_PY="$SCRIPT_DIR/bin/aic.py"
    PROFILE_BLOCK=$(cat << EOF
# >>> AIC >>>
aic() {
    "$PYTHON_BIN" "$AIC_PY" "\$@"
}
codex() {
    if [ -f "$SYNC_HELPER" ]; then
        "$PYTHON_BIN" "$SYNC_HELPER"
    fi
    command codex "\$@"
}
# <<< AIC <<<
EOF
)
    PROFILE_STATE_FILE="$CODEX_DIR/aic_profile_rollback.json"
    "$PYTHON_BIN" "$SCRIPT_DIR/scripts/manage_profile.py" --profile "$PROFILE_FILE" --action install --block-text "$PROFILE_BLOCK" --state-file "$PROFILE_STATE_FILE" || rollback
    STATE_PROFILE_ADDED=1
    echo "-> Da dang ky ham 'aic' & 'codex' Smart Wrapper vao $PROFILE_FILE"
fi

echo "=== Khoi dong CLIProxyAPI ==="
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

# Clean up profile transaction state on install success
rm -f "$PROFILE_STATE_FILE" 2>/dev/null || true

echo -e "\n🎉 AIC installed successfully! Run 'aic' or 'codex' to get started.\n"
