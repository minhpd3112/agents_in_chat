#!/usr/bin/env bash
# ==============================================================================
#  agents_in_chat: Factory Reset / Uninstaller for Linux / macOS / WSL
#  Khoi phuc 100% cai dat goc, Lich su chat & Go bo lenh 'aic'
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_DIR="$HOME/.codex"
MODELS_CACHE="$CODEX_DIR/models_cache.json"

echo "=== Khoi phuc cai dat goc OpenAI Codex CLI ==="

# 1. Tat proxy
"$SCRIPT_DIR/stop.sh"

# 2. Xoa models_cache.json tuy chinh
chmod 644 "$MODELS_CACHE" 2>/dev/null || true
rm -f "$MODELS_CACHE"
echo "-> Da xoa models_cache.json tuy chinh."

# 3. Lam sach config.toml ve OpenAI mac dinh
python3 "$SCRIPT_DIR/scripts/configure_codex_toml.py" openai 2>/dev/null || python "$SCRIPT_DIR/scripts/configure_codex_toml.py" openai

# 4. Dong bo lich su chat ve provider 'openai'
python3 "$SCRIPT_DIR/scripts/sync_sessions.py" openai 2>/dev/null || python "$SCRIPT_DIR/scripts/sync_sessions.py" openai

# 5. Go bo symlink aic
rm -f "$HOME/.local/bin/aic" 2>/dev/null || true
echo "-> Da go bo lenh toan cuc 'aic'."

echo "============================================================"
echo "   DA KHOI PHUC CAI DAT GOC & GO BO 'aic' THANH CONG 100%!"
echo "============================================================"
