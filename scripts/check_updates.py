"""
AIC Self-Update Module (Python Stdlib-only)
Checks for remote updates from GitHub raw content and provides smooth upgrade routines.
"""

import os
import sys
import json
import time
import urllib.request
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent

def get_codex_dir() -> Path:
    override = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if override:
        return Path(override)
    return Path.home() / ".codex"

def get_local_version() -> str:
    v_file = ROOT_DIR / "VERSION"
    if v_file.exists():
        try:
            return v_file.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
        except Exception:
            pass
    return "unknown"

def clean_version_str(v: Any) -> str:
    if not isinstance(v, str):
        v = str(v)
    return v.strip().lstrip("vV\ufeff").strip()

def parse_semver(v: str) -> Tuple[int, ...]:
    clean = clean_version_str(v)
    parts = []
    for piece in clean.split("."):
        digits = ""
        for c in piece:
            if c.isdigit():
                digits += c
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

def is_newer(remote: str, local: str) -> bool:
    try:
        return parse_semver(remote) > parse_semver(local)
    except Exception:
        return False

def get_cache_file() -> Path:
    return get_codex_dir() / "aic_version.json"

def read_cache() -> Dict[str, Any]:
    cache_file = get_cache_file()
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "latest_version" in data:
                data["latest_version"] = clean_version_str(data["latest_version"])
            return data
    except Exception:
        return {}

def write_cache(data: Dict[str, Any]) -> None:
    cache_file = get_cache_file()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp." + str(time.time_ns()))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, cache_file)
    except Exception:
        pass

def fetch_remote_version(timeout: float = 1.5) -> Optional[str]:
    url = "https://raw.githubusercontent.com/minhpd3112/agents_in_chat/main/VERSION"
    req = urllib.request.Request(url, headers={"User-Agent": "AIC-Updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                content = resp.read().decode("utf-8").strip()
                if content and any(c.isdigit() for c in content):
                    return content
    except Exception:
        pass
    return None

def check_for_update(force: bool = False) -> Tuple[bool, str, str]:
    """
    Checks if an update is available.
    Returns (has_update, local_version, latest_version)
    """
    local_ver = get_local_version()
    cache = read_cache()
    now = time.time()
    last_checked = cache.get("last_checked_at", 0)
    cached_remote = cache.get("latest_version", local_ver)

    # Check network if forced or cache older than 24h
    if force or (now - last_checked > 86400):
        remote_ver = fetch_remote_version()
        if remote_ver:
            cached_remote = remote_ver
            write_cache({
                "latest_version": remote_ver,
                "last_checked_at": now
            })
        else:
            # Refresh timestamp even on failure to prevent hammering
            write_cache({
                "latest_version": cached_remote,
                "last_checked_at": now
            })

    has_update = is_newer(cached_remote, local_ver)
    return has_update, local_ver, cached_remote

def prompt_update_if_available() -> bool:
    """
    If running interactively and an update is available, prompt the user.
    Returns True if update was performed, False otherwise.
    """
    # Non-interactive bypass
    if os.environ.get("AIC_NON_INTERACTIVE") == "1" or os.environ.get("AIC_TEST_MODE") == "1":
        return False
    if not sys.stdin.isatty():
        return False

    has_update, local_ver, remote_ver = check_for_update(force=False)
    if not has_update:
        return False

    print(f"\n\033[96m* AIC Update available! v{local_ver} -> v{remote_ver}\033[0m")
    print("  1. Update now")
    print("  2. Skip")
    try:
        choice = input("Select [1-2] (default 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if choice in ("", "1"):
        return run_update()
    return False

def run_update() -> bool:
    """
    Executes git pull, synchronizes models cache, and restarts proxy if online.
    """
    print("-> Pulling latest changes from git repository...")
    git_dir = ROOT_DIR / ".git"
    if not git_dir.exists():
        print("Error: .git directory not found. Please update via git clone.")
        return False

    try:
        proc = subprocess.run(["git", "pull", "origin", "main"], cwd=str(ROOT_DIR), capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"Error updating repository:\n{proc.stderr}")
            return False
        print(proc.stdout.strip())
    except Exception as e:
        print(f"Failed to execute git pull: {e}")
        return False

    # Synchronize models_cache.json
    try:
        cache_path = get_codex_dir() / "models_cache.json"
        template_path = ROOT_DIR / "docs" / "models_cache_template.json"
        if template_path.exists() and cache_path.exists():
            codex_ver = "0.153.0"
            try:
                vout = subprocess.run(["codex", "--version"], capture_output=True, text=True)
                import re
                m = re.search(r"(\d+\.\d+\.\d+)", vout.stdout)
                if m:
                    codex_ver = m.group(1)
            except Exception:
                pass

            with open(template_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            tdata["client_version"] = codex_ver

            # Unlock Read-Only, write, re-lock
            if sys.platform == "win32":
                subprocess.run(["attrib", "-r", str(cache_path)], capture_output=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(tdata, f, indent=2, ensure_ascii=False)
            if sys.platform == "win32":
                subprocess.run(["attrib", "+r", str(cache_path)], capture_output=True)
            else:
                os.chmod(cache_path, 0o444)
    except Exception as e:
        print(f"Warning: Failed to sync models_cache: {e}")

    # Check if proxy is currently running
    proxy_was_running = False
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/v1/models")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                proxy_was_running = True
    except Exception:
        pass

    if proxy_was_running:
        print("-> Restarting CLIProxyAPI service...")
        aic_py = ROOT_DIR / "bin" / "aic.py"
        subprocess.run([sys.executable, str(aic_py), "restart"], cwd=str(ROOT_DIR))

    new_ver = get_local_version()
    print(f"\033[92m-> AIC updated to v{new_ver} successfully!\033[0m\n")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        has_up, lver, rver = check_for_update(force=True)
        print(f"Local: {lver}, Remote: {rver}, Update Available: {has_up}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--run":
        run_update()
    else:
        prompt_update_if_available()
