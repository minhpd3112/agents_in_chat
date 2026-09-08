#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  agents_in_chat: Factory Reset / Uninstaller for Linux / macOS / WSL
#  Khoi phuc cai dat goc, Lich su chat & Go bo lenh 'aic'
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 0. Test Mode & Environment Isolation
AIC_TEST_MODE="${AIC_TEST_MODE:-0}"
CODEX_DIR="${AIC_CODEX_DIR:-$HOME/.codex}"
BIN_LINK_DIR="${AIC_BIN_LINK_DIR:-$HOME/.local/bin}"
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
    echo "[ERROR] Khong tim thay Python! Vui long cai dat Python truoc khi chay uninstall."
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


echo "=== Khoi phuc cai dat goc OpenAI Codex CLI ==="


# 1. Tat proxy
echo "-> Dang tat tien trinh Proxy API..."
if [ "$AIC_SKIP_PROXY" -ne 1 ]; then
    "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
else
    echo "-> [TEST_MODE] Bo qua tat proxy."
fi

# 2. Dong bo toan bo lich su chat ve provider 'openai'
echo "-> Dong bo toan bo lich su chat ve provider 'openai'..."
if ! "$PYTHON_BIN" "$SYNC_SCRIPT" openai; then
    echo -e "\n[ERROR] Dong bo lich su chat sang 'openai' that bai! Rollback session ve 'custom' va huy bo uninstall."
    "$PYTHON_BIN" "$SYNC_SCRIPT" custom >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" --verify custom >/dev/null 2>&1 || true
    exit 1
fi

# 3. Xac minh toan bo lich su chat da chuyen sang 'openai'
echo "-> Xac minh toan bo lich su chat sang 'openai'..."
if ! "$PYTHON_BIN" "$SYNC_SCRIPT" --verify openai; then
    echo -e "\n[ERROR] Xac minh lich su chat that bai! Rollback session ve 'custom' va huy bo uninstall."
    "$PYTHON_BIN" "$SYNC_SCRIPT" custom >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" --verify custom >/dev/null 2>&1 || true
    exit 1
fi

# 4. Khoi phuc config.toml ban dau
echo "-> Khoi phuc config.toml ban dau..."
if ! "$PYTHON_BIN" "$CONFIG_SCRIPT" restore; then
    echo -e "\n[ERROR] Khoi phuc config.toml that bai! Rollback session ve 'custom' va huy bo uninstall."
    "$PYTHON_BIN" "$SYNC_SCRIPT" custom >/dev/null 2>&1 || true
    "$PYTHON_BIN" "$SYNC_SCRIPT" --verify custom >/dev/null 2>&1 || true
    exit 1
fi

# 5. Xoa models_cache.json tuy chinh
echo "-> Xu ly models_cache.json tuy chinh..."
if [ "$AIC_FAIL_STEP" = "remove-cache" ]; then
    echo "[FAIL_INJECTION] Injected failure at remove-cache"
    exit 1
fi
if [ -f "$MODELS_CACHE" ]; then
    chmod 644 "$MODELS_CACHE" 2>/dev/null || true
    rm -f "$MODELS_CACHE"
    if [ -f "$MODELS_CACHE" ]; then
        echo "[ERROR] Khong the xoa models_cache.json tai $MODELS_CACHE"
        exit 1
    fi
    echo "-> Da xoa models_cache.json tuy chinh."
fi

# 6. Go bo symlink aic (Chi xoa neu la symlink trỏ toi aic)
echo "-> Go bo lenh toan cuc 'aic'..."
if [ -L "$BIN_LINK_DIR/aic" ]; then
    rm -f "$BIN_LINK_DIR/aic"
    echo "-> Da go bo symlink toan cuc 'aic'."
fi

# 6b. Go bo block 'aic' khoi profile
echo "-> Go bo block 'aic' khoi profile..."
PROFILE_FILE=""
if [ -n "${AIC_PROFILE_PATH:-}" ]; then
    PROFILE_FILE="$AIC_PROFILE_PATH"
elif [ -f "$HOME/.zshrc" ]; then
    PROFILE_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PROFILE_FILE="$HOME/.bashrc"
fi
if [ -n "$PROFILE_FILE" ] && [ -f "$PROFILE_FILE" ]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/scripts/manage_profile.py" --profile "$PROFILE_FILE" --action uninstall
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "[ERROR] Go bo block 'aic' khoi profile that bai."
        exit $rc
    fi
    echo "-> Da go bo block 'aic' khoi $PROFILE_FILE"
fi
PROFILE_STATE_FILE="$CODEX_DIR/aic_profile_rollback.json"
rm -f "$PROFILE_STATE_FILE" 2>/dev/null || true

# 7. Don dep thu muc aic-backup
"$PYTHON_BIN" "$CONFIG_SCRIPT" clean-backup >/dev/null 2>&1 || true


echo "============================================================"
echo "   DA KHOI PHUC CAI DAT GOC & GO BO 'aic' THANH CONG 100%!"
echo "============================================================"
echo "Cau hinh Codex CLI da duoc khoi phuc ve provider OpenAI goc,"
echo "toan bo lich su chat cu & moi duoc bao toan va co the tiep tuc resume."
