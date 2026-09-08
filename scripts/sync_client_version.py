#!/usr/bin/env python3
# ==============================================================================
#  sync_client_version.py - Smart Wrapper Hook to synchronize Codex client_version
#  Python stdlib-only. Works across Windows, Linux, and macOS.
# ==============================================================================

import os
import sys
import json
import re
import time
import subprocess
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from log_utils import error, warn  # noqa: E402


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


def get_codex_dir() -> Path:
    override = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if override:
        return Path(override)
    return Path.home() / ".codex"


def get_codex_version(codex_bin: Optional[str] = None) -> Optional[str]:
    cmd = [codex_bin] if codex_bin else ["codex", "--version"]
    if codex_bin and not codex_bin.endswith("--version"):
        cmd = [codex_bin, "--version"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
        out = res.stdout if res.stdout else res.stderr
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def sync_version(codex_bin: Optional[str] = None) -> int:
    cache_path = get_codex_dir() / "models_cache.json"
    if not cache_path.exists():
        return 0

    current_ver = get_codex_version(codex_bin)
    if not current_ver:
        # Cannot determine version; leave cache alone and allow codex to run
        return 0

    # Read existing cache
    try:
        raw_text = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        cached_ver = data.get("client_version")
        if cached_ver == current_ver:
            # Already matches, ensure it remains read-only
            if not is_file_readonly(cache_path):
                if sys.platform == "win32":
                    subprocess.run(["attrib", "+r", str(cache_path)], capture_output=True)
                else:
                    try:
                        os.chmod(cache_path, 0o444)
                    except Exception:
                        pass
                if not is_file_readonly(cache_path):
                    error(f"failed to lock models_cache.json read-only at {cache_path}")
                    return 1
            return 0
        data["client_version"] = current_ver
        new_content = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception as e:
        error(f"unable to parse models_cache.json: {e}")
        return 1

    # Step 1: Write and validate temp file first
    tmp_path = cache_path.with_name(f"{cache_path.name}.tmp.{time.time_ns()}")
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())

        # Validate temp file JSON
        with open(tmp_path, "r", encoding="utf-8") as f:
            vdata = json.load(f)
            if vdata.get("client_version") != current_ver:
                raise ValueError("Validation of updated client_version failed")
    except Exception as e:
        error(f"failed to prepare models_cache update: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return 1

    # Step 2: Unlock target, replace, and ALWAYS relock in finally
    replace_ok = False
    try:
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
        error(f"failed to update models_cache.json: {e}")
    finally:
        # Step 3: Relock cache file in all cases
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_path)], capture_output=True)
        else:
            try:
                os.chmod(cache_path, 0o444)
            except Exception:
                pass
        # Clean temp file if still present
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    if not replace_ok:
        return 1

    # Step 4: Verify read-only after relock
    if not is_file_readonly(cache_path):
        error(f"models_cache.json is not read-only after sync at {cache_path}")
        return 1

    return 0


def main() -> int:
    codex_bin = None
    if len(sys.argv) > 1 and sys.argv[1] != "":
        codex_bin = sys.argv[1]
    return sync_version(codex_bin)


if __name__ == "__main__":
    sys.exit(main())
