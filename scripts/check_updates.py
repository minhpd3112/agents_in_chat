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

def validate_models_template(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    models = data.get("models")
    if not isinstance(models, list) or len(models) == 0:
        return False
    for m in models:
        if not isinstance(m, dict) or "slug" not in m:
            return False
        # Project invariant: apply_patch_tool_type, tool_mode, visibility
        if m.get("apply_patch_tool_type") != "freeform":
            return False
        if m.get("tool_mode") != "direct":
            return False
        if m.get("visibility") != "list":
            return False
    return True


def is_file_readonly(path: Path) -> bool:
    if not path.exists():
        return False
    if sys.platform == "win32":
        import stat
        try:
            mode = os.stat(path).st_mode
            return not bool(mode & stat.S_IWRITE)
        except Exception:
            return False
    else:
        return not os.access(path, os.W_OK)


def is_cache_semantically_valid(cache_path: Path, expected_data: Dict[str, Any]) -> bool:
    if not cache_path.exists():
        return False
    if not is_file_readonly(cache_path):
        return False
    try:
        raw = cache_path.read_text(encoding="utf-8")
        current_data = json.loads(raw)
        return current_data == expected_data
    except Exception:
        return False


def reconcile_models_cache(repo_dir: Path, codex_dir: Path) -> bool:
    template_path = repo_dir / "docs" / "models_cache_template.json"
    cache_path = codex_dir / "models_cache.json"

    if not template_path.exists():
        print(f"Error: Template {template_path} does not exist.")
        return False

    try:
        raw_tpl = template_path.read_text(encoding="utf-8")
        tdata = json.loads(raw_tpl)
        if not validate_models_template(tdata):
            print("Error: Template models_cache_template.json failed invariant validation.")
            return False
    except Exception as e:
        print(f"Error: Invalid JSON in template {template_path}: {e}")
        return False

    # Get codex version
    codex_ver = None
    try:
        vout = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=2.0)
        import re
        m = re.search(r"(\d+\.\d+\.\d+)", vout.stdout if vout.stdout else vout.stderr)
        if m:
            codex_ver = m.group(1)
    except Exception:
        pass

    import copy
    expected_data = copy.deepcopy(tdata)
    if codex_ver:
        expected_data["client_version"] = codex_ver

    # Semantic comparison: checks JSON content recursively and read-only flag
    if is_cache_semantically_valid(cache_path, expected_data):
        return True

    print("-> Reconciling ~/.codex/models_cache.json...")
    new_content = json.dumps(expected_data, indent=2, ensure_ascii=False)

    tmp_path = cache_path.with_name(f"{cache_path.name}.tmp.{time.time_ns()}")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())

        # Validate temp file
        with open(tmp_path, "r", encoding="utf-8") as f:
            vdata = json.load(f)
            if vdata != expected_data:
                raise ValueError("Validation failed on written cache temp file.")
    except Exception as e:
        print(f"Error: Failed to prepare models_cache update: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False

    # Unlock, replace, and relock in finally
    replace_ok = False
    try:
        if cache_path.exists():
            if sys.platform == "win32":
                subprocess.run(["attrib", "-r", str(cache_path)], capture_output=True)
            else:
                try:
                    os.chmod(cache_path, 0o644)
                except Exception:
                    pass

        os.replace(tmp_path, cache_path)
        replace_ok = True
    except Exception as e:
        print(f"Error: Failed to replace models_cache.json: {e}")
        return False
    finally:
        if cache_path.exists():
            if sys.platform == "win32":
                subprocess.run(["attrib", "+r", str(cache_path)], capture_output=True)
            else:
                try:
                    os.chmod(cache_path, 0o444)
                except Exception:
                    pass
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    if not replace_ok:
        return False

    # Verify read-only after relock
    if not is_file_readonly(cache_path):
        print(f"Error: Failed to relock models_cache.json read-only at {cache_path}")
        return False

    return True


def run_update(repo_dir: Optional[Path] = None) -> bool:
    """
    Executes a safe two-phase update:
      Phase 1: Code update (Git fast-forward with clean worktree assertion)
      Phase 2: Runtime reconciliation (models_cache validation, proxy health check)
    """
    if repo_dir is None:
        repo_dir = ROOT_DIR
    codex_dir = get_codex_dir()

    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        print(f"Error: .git directory not found in {repo_dir}. Cannot update via git.")
        return False

    # 1. Branch verification
    branch_proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_dir), capture_output=True, text=True
    )
    if branch_proc.returncode != 0:
        print(f"Error: Failed to determine git branch:\n{branch_proc.stderr}")
        return False

    current_branch = branch_proc.stdout.strip()
    if current_branch != "main":
        print(f"Error: Current branch is '{current_branch}', not 'main'. Aborting update.")
        return False

    # 2. Remote tracking verification
    remotes_proc = subprocess.run(
        ["git", "remote"],
        cwd=str(repo_dir), capture_output=True, text=True
    )
    if "origin" not in remotes_proc.stdout.split():
        print("Error: Git remote 'origin' is not configured. Aborting update.")
        return False

    # 3. Clean worktree check
    # Check staged changes
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo_dir))
    if staged.returncode != 0:
        print("Error: Worktree has staged changes. Aborting update to avoid losing data.")
        return False

    # Check unstaged changes
    unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=str(repo_dir))
    if unstaged.returncode != 0:
        print("Error: Worktree has unstaged modifications. Aborting update to avoid losing data.")
        return False

    # Check untracked unignored files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(repo_dir), capture_output=True, text=True
    )
    if untracked.stdout.strip():
        print("Error: Worktree has untracked files:\n" + untracked.stdout.strip() + "\nAborting update.")
        return False

    # Record old SHA
    old_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir), capture_output=True, text=True
    ).stdout.strip()

    # Determine if proxy is managed and running before update
    sys.path.insert(0, str(repo_dir / "scripts"))
    try:
        import proxy_manager
        p_status, _ = proxy_manager.verify_managed_state()
        proxy_was_running = (p_status == "healthy")
    except Exception:
        proxy_was_running = False

    # 4. Fetch origin main
    print("-> Fetching updates from origin/main...")
    fetch_proc = subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=str(repo_dir), capture_output=True, text=True
    )
    if fetch_proc.returncode != 0:
        print(f"Error: Network or git fetch failure:\n{fetch_proc.stderr.strip()}")
        return False

    # Get remote commit SHA
    remote_sha_proc = subprocess.run(
        ["git", "rev-parse", "FETCH_HEAD"],
        cwd=str(repo_dir), capture_output=True, text=True
    )
    remote_sha = remote_sha_proc.stdout.strip()
    if not remote_sha or remote_sha_proc.returncode != 0:
        remote_sha = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=str(repo_dir), capture_output=True, text=True
        ).stdout.strip()

    code_changed = (old_sha != remote_sha)
    new_sha = old_sha

    if code_changed:
        # Check fast-forward possibility
        ff_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old_sha, remote_sha],
            cwd=str(repo_dir)
        )
        if ff_check.returncode != 0:
            print("Error: Fast-forward not possible (local and remote have diverged). Manual merge required.")
            return False

        # Execute ff-only merge
        merge_proc = subprocess.run(
            ["git", "merge", "--ff-only", remote_sha],
            cwd=str(repo_dir), capture_output=True, text=True
        )
        if merge_proc.returncode != 0:
            print(f"Error: Fast-forward merge failed:\n{merge_proc.stderr}")
            return False

        new_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True
        ).stdout.strip()
        print(f"-> Code fast-forwarded: {old_sha[:7]} -> {new_sha[:7]}")
    else:
        print(f"-> Code is already up to date ({old_sha[:7]}).")

    # Phase 2: Runtime Reconciliation
    cache_ok = reconcile_models_cache(repo_dir, codex_dir)
    if not cache_ok:
        if code_changed:
            print(f"Warning: Code was updated ({old_sha[:7]} -> {new_sha[:7]}), but cache reconciliation failed.")
        return False

    # Restart proxy if code changed and proxy was running
    if code_changed and proxy_was_running:
        print("-> Restarting managed CLIProxyAPI service...")
        try:
            import proxy_manager
            rc = proxy_manager.restart_proxy()
            if rc != 0:
                print("Error: Proxy restart failed.")
                print(f"Warning: Code was updated ({old_sha[:7]} -> {new_sha[:7]}), but proxy restart failed.")
                return False
        except Exception as e:
            print(f"Error restarting proxy: {e}")
            return False

    new_ver = get_local_version()
    if code_changed:
        print(f"\033[92m-> AIC updated to v{new_ver} ({old_sha[:7]} -> {new_sha[:7]}) successfully!\033[0m\n")
    else:
        print(f"\033[92m-> AIC is up-to-date (v{new_ver} at {old_sha[:7]}). System verified healthy.\033[0m\n")

    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        has_up, lver, rver = check_for_update(force=True)
        print(f"Local: {lver}, Remote: {rver}, Update Available: {has_up}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--run":
        ok = run_update()
        sys.exit(0 if ok else 1)
    else:
        prompt_update_if_available()
