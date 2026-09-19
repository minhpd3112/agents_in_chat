#!/usr/bin/env python3
"""Codex config.toml manager: CLI wrapper and backwards-compatibility entrypoint.

Underlying logic is maintained in scripts/config_manager.py.
"""

import argparse
import os
import sys
from pathlib import Path

from config_manager import (
    atomic_write_bytes,
    clean_aic_keys_legacy,
    clean_backup_dir,
    compute_sha256_bytes,
    compute_sha256_file,
    configure_custom,
    ensure_backup,
    get_proxy_port,
    is_legacy_aic_instruction,
    restore_original,
    split_toml_sections,
    validate_existing_manifest,
)
from log_utils import error, info, warn


def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Agents in Chat - Codex Config TOML Manager")
    parser.add_argument("action", nargs="?", default="custom", help="Action: 'custom', 'openai', 'restore', 'backup', 'clean-backup'")
    parser.add_argument("--codex-dir", default=None, help="Custom path to .codex directory")

    args = parser.parse_args()
    codex_dir = get_codex_dir(args.codex_dir)
    action = args.action.lower()

    if action in ["custom", "install", "aic"]:
        return configure_custom(codex_dir)
    elif action in ["openai", "restore", "uninstall"]:
        return restore_original(codex_dir)
    elif action in ["backup"]:
        ok = ensure_backup(codex_dir)
        return 0 if ok else 1
    elif action in ["clean-backup", "clean_backup"]:
        return clean_backup_dir(codex_dir)
    else:
        error(f"unknown action: {action}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
