#!/usr/bin/env python3
"""Repair orchestration module for AIC.

Orchestrates:
1. Auto-termination of lingering codex processes (Windows: taskkill, Unix: pkill).
2. Configuration setup for config.toml via configure_custom.
3. Models cache template reconciliation & read-only lock.
4. Structured session history sanitization & lineage realignment.
5. SQLite projection cache clearing.
6. Verification of provider, lineage, and instruction integrity.

Strictly checks each step's return code and never reports false success.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from log_utils import error, info, warn


def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def is_codex_running() -> bool:
    if os.environ.get("AIC_TEST_MODE") == "1":
        return os.environ.get("AIC_MOCK_CODEX_RUNNING") == "1"

    try:
        if sys.platform == "win32":
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq codex.exe", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "codex.exe" in res.stdout.lower()
        else:
            res = subprocess.run(
                ["pgrep", "-x", "codex"],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0
    except Exception as e:
        warn(f"could not determine if codex is running: {e}")
        return False


def kill_codex() -> bool:
    """Terminate running codex processes to release file locks.
    In test mode (AIC_TEST_MODE=1), honors AIC_MOCK_KILL_FAIL."""
    if os.environ.get("AIC_TEST_MODE") == "1":
        if os.environ.get("AIC_MOCK_KILL_FAIL") == "1":
            return False
        os.environ["AIC_MOCK_CODEX_RUNNING"] = "0"
        return True

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "codex.exe"], capture_output=True, check=False)
        else:
            subprocess.run(["pkill", "-9", "-x", "codex"], capture_output=True, check=False)
        time.sleep(0.3)
        return not is_codex_running()
    except Exception as e:
        error(f"failed to kill codex process: {e}")
        return False


def run_repair(target_codex_dir: Optional[Path] = None, root_dir: Optional[Path] = None) -> int:
    """Orchestrate system repair with strict failure handling."""
    codex_dir = target_codex_dir or get_codex_dir()
    repo_root = root_dir or Path(__file__).resolve().parent.parent

    print("=" * 65)
    print("                    AIC REPAIR SYSTEM & CACHE")
    print("=" * 65)

    # 1. Release file locks: auto-terminate lingering codex process if detected
    if is_codex_running():
        info("Detected running codex.exe -> Auto-terminating process to release file locks...")
        if not kill_codex():
            error("Failed to terminate running codex process; aborting repair to avoid file-lock conflicts.")
            return 1
        if is_codex_running():
            error("Codex process is still running after termination attempt; aborting repair.")
            return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-kill":
        error("injected failure at repair-kill")
        return 1

    # 2. Detect provider and configure config.toml
    prov = "custom"
    config_file = codex_dir / "config.toml"

    from config_manager import configure_custom

    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8")
            if 'model_provider = "openai"' in content:
                prov = "openai"
            else:
                cfg_rc = configure_custom(codex_dir)
                if cfg_rc != 0:
                    error(f"Failed to configure config.toml (exit code {cfg_rc}).")
                    return 1
        except Exception as e:
            error(f"Error reading or configuring config.toml: {e}")
            return 1
    else:
        info("config.toml not found; configuring default custom provider...")
        cfg_rc = configure_custom(codex_dir)
        if cfg_rc != 0:
            error(f"Failed to create config.toml (exit code {cfg_rc}).")
            return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-configure":
        error("injected failure at repair-configure")
        return 1

    # 3. Reconcile models_cache.json from template & lock Read-Only
    print("[1/4] Reconciling models_cache.json from template...")
    from check_updates import reconcile_models_cache

    if not reconcile_models_cache(repo_root, codex_dir):
        error("Failed to reconcile models_cache.json from template.")
        return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-models-cache":
        error("injected failure at repair-models-cache")
        return 1

    info("models_cache.json successfully reconciled and locked Read-Only.")

    # 4. Structured session sanitization & lineage realignment
    print(f"[2/4] Sanitizing session histories and re-aligning lineages ({prov})...")
    from sync_sessions import sync_provider, verify_provider

    sync_rc = sync_provider(prov, codex_dir)
    if sync_rc != 0:
        error(f"Failed to sanitize and sync session histories (exit code {sync_rc}).")
        return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-sync":
        error("injected failure at repair-sync")
        return 1

    # 5. Clear SQLite history projection cache
    print("[3/4] Clearing SQLite history projection cache...")
    from sync_sessions import clear_history_projection_cache

    if not clear_history_projection_cache(codex_dir):
        error("Failed to clear history projection cache in SQLite.")
        return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-sqlite-cache":
        error("injected failure at repair-sqlite-cache")
        return 1

    # 6. Verify provider & lineage integrity
    print("[4/4] Verifying provider integrity...")
    verify_rc = verify_provider(prov, codex_dir, check_instructions=True, check_lineage=True)
    if verify_rc != 0:
        error("Provider and session verification failed.")
        return 1

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "repair-verify":
        error("injected failure at repair-verify")
        return 1

    print("=" * 65)
    info("System repair completed successfully! You can now resume or switch models safely.")
    return 0


if __name__ == "__main__":
    sys.exit(run_repair())
