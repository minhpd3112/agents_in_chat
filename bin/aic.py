#!/usr/bin/env python3
# ==============================================================================
#  aic - Agents in Chat Multi-Model Quota Pool CLI Manager
#  Inspired by anoti ergonomic CLI pattern
# ==============================================================================

import os
import sys
import json
import urllib.request
import subprocess
from pathlib import Path

def get_root_dir() -> Path:
    script_dir = Path(__file__).resolve().parent
    if (script_dir.parent / "config.yaml").exists() or (script_dir.parent / "config.example.yaml").exists() or (script_dir.parent / "install.ps1").exists():
        return script_dir.parent
    if (script_dir / "config.yaml").exists() or (script_dir / "config.example.yaml").exists() or (script_dir / "install.ps1").exists():
        return script_dir
    return script_dir.parent

ROOT_DIR = get_root_dir()
CODEX_DIR = Path(os.path.expanduser("~/.codex"))

sys.path.insert(0, str(ROOT_DIR / "scripts"))
from log_utils import error, info, warn  # noqa: E402
from check_updates import get_local_version, check_for_update, prompt_update_if_available, run_update  # noqa: E402
from proxy_manager import start_proxy, stop_proxy, restart_proxy, check_proxy_health, get_proxy_port  # noqa: E402

VERSION = get_local_version()

def ensure_default_compat_auth():
    auth_dir = ROOT_DIR / "auths"
    auth_dir.mkdir(parents=True, exist_ok=True)
    zen_auth = auth_dir / "openai-compatible-opencode-zen.json"
    if not zen_auth.exists():
        auth_data = {
            "type": "openai-compatible",
            "provider": "openai-compatible-opencode-zen",
            "name": "opencode-zen",
            "url": "https://opencode.ai/zen/v1",
            "base_url": "https://opencode.ai/zen/v1",
            "key": "public",
            "api_key": "public",
            "models": ["muse-spark-1.3-contributor-free", "muse-spark-1.3"]
        }
        zen_auth.write_text(json.dumps(auth_data, indent=2), encoding="utf-8")

def cmd_start() -> int:
    prompt_update_if_available()
    ensure_default_compat_auth()
    return start_proxy()

def cmd_stop() -> int:
    return stop_proxy()

def cmd_restart() -> int:
    return restart_proxy()


def is_codex_running() -> bool:
    if os.environ.get("AIC_TEST_MODE") == "1":
        return os.environ.get("AIC_MOCK_CODEX_RUNNING") == "1"
    try:
        if sys.platform == "win32":
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq codex.exe", "/NH"],
                capture_output=True,
                text=True,
                check=False
            )
            return "codex.exe" in res.stdout.lower()
        else:
            res = subprocess.run(
                ["pgrep", "-x", "codex"],
                capture_output=True,
                text=True,
                check=False
            )
            return res.returncode == 0
    except Exception:
        return False


def kill_codex() -> bool:
    """Terminate running codex processes to release file locks."""
    if os.environ.get("AIC_TEST_MODE") == "1":
        return True
    try:
        import time
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "codex.exe"], capture_output=True, check=False)
        else:
            subprocess.run(["pkill", "-9", "-x", "codex"], capture_output=True, check=False)
        time.sleep(0.3)
        return not is_codex_running()
    except Exception:
        return False


def cmd_repair(target_codex_dir: Path = None) -> int:
    """Repair multi-model switching and cache issues:
    - Auto-terminates running codex.exe if detected to prevent file-lock conflicts.
    - Reconciles models_cache.json from template & locks Read-Only.
    - Sanitizes session history (developer <model_switch> / instructions).
    - Re-aligns forked lineages.
    - Clears history projection cache.
    - Verifies integrity.
    """
    codex_dir = target_codex_dir or Path(os.environ.get("AIC_CODEX_DIR") or CODEX_DIR)

    print("=" * 65)
    print("                    AIC REPAIR SYSTEM & CACHE")
    print("=" * 65)

    # 1. Release file locks: auto-terminate lingering codex process if detected
    if is_codex_running():
        info("Detected running codex.exe -> Auto-terminating process to release file locks...")
        kill_codex()

    # 2. Detect current provider and ensure config.toml has instructions configured
    prov = "custom"
    config_file = codex_dir / "config.toml"
    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8")
            if 'model_provider = "openai"' in content:
                prov = "openai"
            else:
                from configure_codex_toml import configure_custom
                configure_custom(codex_dir)
        except Exception:
            pass

    # 3. Reconcile models_cache.json
    print("[1/4] Reconciling models_cache.json from template...")
    from check_updates import reconcile_models_cache
    if not reconcile_models_cache(ROOT_DIR, codex_dir):
        error("Failed to reconcile models_cache.json from template.")
        return 1
    info("models_cache.json successfully reconciled and locked Read-Only.")

    # 4. Structured session sanitization & lineage realignment
    print(f"[2/4] Sanitizing session histories and re-aligning lineages ({prov})...")
    from sync_sessions import sync_provider, verify_provider
    sync_rc = sync_provider(prov, codex_dir)
    if sync_rc != 0:
        error(f"Failed to sanitize and sync session histories (exit code {sync_rc}).")
        return 1

    # 5. Clear history projection cache
    print("[3/4] Clearing SQLite history projection cache...")
    from sync_sessions import clear_history_projection_cache
    clear_history_projection_cache(codex_dir)

    # 6. Verify provider & lineage integrity
    print("[4/4] Verifying provider integrity...")
    verify_rc = verify_provider(prov, codex_dir, check_instructions=True)
    if verify_rc != 0:
        error("Provider and session verification failed.")
        return 1

    print("=" * 65)
    info("System repair completed successfully! You can now resume or switch models safely.")
    return 0


# Backwards compatibility alias
cmd_repair_antigravity = cmd_repair



def cmd_status() -> int:
    print("=" * 65)
    print(f"  AGENTS IN CHAT (AIC) SYSTEM STATUS  |  v{VERSION}")
    print("=" * 65)

    # Auth count is needed by both the Provider label and section 4
    auths_dir = ROOT_DIR / "auths"
    auth_count = len(list(auths_dir.glob("*.json"))) if auths_dir.exists() else 0

    # 1. Proxy
    port = get_proxy_port()
    online, models = check_proxy_health(port)
    if online:
        models_str = ", ".join(models)
        print(f"[OK] Proxy Service (127.0.0.1:{port}) : ONLINE [200 OK]")
        print(f"     -> Models Online ({len(models)}): {models_str}")
    else:
        print(f"[OFFLINE] Proxy Service (127.0.0.1:{port}) : OFFLINE")

    # 2. Config Provider
    config_file = CODEX_DIR / "config.toml"
    provider_str = "Chua ro"
    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8")
            if "model_provider = \"custom\"" in content:
                provider_str = f"custom (Agents Quota Pool - {auth_count} OAuth Accounts)"
            elif "model_provider = \"openai\"" in content:
                provider_str = "openai (Vanilla OpenAI Native)"
        except Exception:
            pass
    print(f"[*]  Codex Model Provider       : {provider_str}")

    # 3. Models Cache Lock
    cache_file = CODEX_DIR / "models_cache.json"
    if cache_file.exists():
        if sys.platform == "win32":
            import stat
            is_ro = bool(os.stat(cache_file).st_mode & stat.S_IREAD) and not bool(os.stat(cache_file).st_mode & stat.S_IWRITE)
        else:
            is_ro = not os.access(cache_file, os.W_OK)
        lock_str = "DA KHOA [Read-Only] (Chong ghi de ETag 100%)" if is_ro else "CHUA KHOA [Writable]"
        print(f"[LOCK] Models Cache Protection    : {lock_str}")
    else:
        print(f"[LOCK] Models Cache Protection    : File cache chua duoc tao")

    # 4. Auth Accounts
    print(f"[AUTH] OAuth Quota Accounts       : {auth_count} tai khoan san sang")

    # 5. Version / Update Status
    has_up, lver, rver = check_for_update(force=False)
    if has_up:
        print(f"[VER]  AIC System Version        : v{lver} (Update available -> v{rver} | Run 'aic update')")
    else:
        print(f"[VER]  AIC System Version        : v{lver} (Latest)")
    print("=" * 65)
    return 0 if online else 1

def cmd_update() -> int:
    ok = run_update()
    return 0 if ok else 1

def cmd_test() -> int:
    test_runner = ROOT_DIR / "tests" / "run_tests.py"
    if not test_runner.exists():
        print(f"[ERROR] Khong tim thay bo test tai {test_runner}!")
        return 1
    return subprocess.run([sys.executable, str(test_runner)]).returncode

def cmd_uninstall() -> int:
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "uninstall.ps1"
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)]).returncode
    else:
        sh_script = ROOT_DIR / "uninstall.sh"
        return subprocess.run(["bash", str(sh_script)]).returncode

def run_login_filtered(args) -> int:
    import re
    state = {"in_ssh_banner": False}
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )
        for line in iter(proc.stdout.readline, ''):
            line_clean = line.strip()
            # 1. Version banner & logger timestamps
            if line_clean.startswith("CLIProxyAPI Version:"):
                continue
            if re.match(r'^\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]\s+\[.*?\]', line_clean):
                continue
            # 2. SSH Tunnel detection banner
            if "To authenticate from a remote machine, an SSH tunnel may be required." in line_clean:
                state["in_ssh_banner"] = True
                continue
            if state.get("in_ssh_banner"):
                if "Visit the following URL" in line_clean or line_clean.startswith("https://") or "http://" in line_clean or "Enter code:" in line_clean or "Enter verification code" in line_clean:
                    state["in_ssh_banner"] = False
                else:
                    continue
            # 3. Specific noisy phrases & SSH separator boxes
            if "Run one of the following commands on your local machine" in line_clean:
                continue
            if line_clean.startswith("ssh -L ") or line_clean.startswith("ssh -i "):
                continue
            if "NOTE: If your server's SSH port is not 22" in line_clean:
                continue
            if line_clean.startswith("===") and len(line_clean) > 40:
                continue

            print(line, end="", flush=True)

        proc.stdout.close()
        return proc.wait()
    except KeyboardInterrupt:
        info("login cancelled")
        return 130

def cmd_login_agy() -> int:
    proxy_exe = ROOT_DIR / "cli-proxy-api.exe" if sys.platform == "win32" else ROOT_DIR / "cli-proxy-api"
    if not proxy_exe.exists():
        print(f"[ERROR] Khong tim thay binary tai {proxy_exe}!")
        return 1

    print("=" * 68)
    print("   DANG NHAP GOOGLE ANTIGRAVITY")
    print("=" * 68)
    print("-> Che do Link thu cong (Khong tu dong bat trinh duyet).")
    print("-> Vui long click hoac copy duong link duoi day dan vao trinh duyet:")
    print("-" * 68)
    args = [str(proxy_exe), "-antigravity-login", "-no-browser"]
    return run_login_filtered(args)

def cmd_login_codex(mode=None) -> int:
    proxy_exe = ROOT_DIR / "cli-proxy-api.exe" if sys.platform == "win32" else ROOT_DIR / "cli-proxy-api"
    if not proxy_exe.exists():
        print(f"[ERROR] Khong tim thay binary tai {proxy_exe}!")
        return 1

    if not mode:
        print("=" * 68)
        print("        DANG NHAP TAI KHOAN OPENAI CODEX")
        print("=" * 68)
        print("Chon phuong thuc xac thuc:")
        print("  [1] Browser OAuth Flow (Hien thi link xac thuc de click/copy)")
        print("  [2] Device Code Flow   (Nhap ma xac thuc tren auth.openai.com/codex/device)")
        print("-" * 68)
        try:
            choice = input("Lua chon cua ban: ").strip()
        except (KeyboardInterrupt, EOFError):
            info("login cancelled")
            return 130
        except Exception:
            choice = "1"
        mode = "device" if choice == "2" else "browser"

    mode = mode.lower()
    if mode in ["device", "code", "device-code", "device_code", "2"]:
        print("\n[AIC] Dang khoi dong Device Code Flow...")
        print("-> Vui long truy cap https://auth.openai.com/codex/device va nhap ma:")
        print("-" * 68)
        args = [str(proxy_exe), "-codex-device-login", "-no-browser"]
    else:
        print("\n[AIC] Dang khoi dong Browser OAuth Flow...")
        print("-> Vui long click hoac copy duong link duoi day dan vao trinh duyet:")
        print("-" * 68)
        args = [str(proxy_exe), "-codex-login", "-no-browser"]

    return run_login_filtered(args)


def _suite_count():
    try:
        sys.path.insert(0, str(ROOT_DIR / "tests"))
        import run_tests
        return len(run_tests.SUITES)
    except Exception:
        return 0


def _model_lines():
    tpl = ROOT_DIR / "docs" / "models_cache_template.json"
    try:
        data = json.loads(tpl.read_text(encoding="utf-8"))
        lines = []
        for i, m in enumerate(data.get("models", []), 1):
            slug = m.get("slug", "?")
            disp = m.get("display_name", "")
            lines.append(f"  {i}. {slug:<30} ({disp})" if disp else f"  {i}. {slug}")
        return lines
    except Exception:
        return []


def build_help_text():
    n = _suite_count()
    test_line = f"  aic test        - Chay bo kiem thu tu dong ({n} test suites)" if n else "  aic test        - Chay bo kiem thu tu dong"
    model_lines = _model_lines()
    if model_lines:
        header = f"Danh sach {len(model_lines)} model trong menu /model cua Codex CLI (theo models_cache_template):"
        model_block = "\n".join([header] + model_lines)
    else:
        model_block = "Danh sach model: khong doc duoc docs/models_cache_template.json"

    return f"""
======================================================================
               AGENTS IN CHAT (AIC) CLI MANAGER v{VERSION}
======================================================================
Su dung: aic <lenh> [tuy chon]

Cac lenh kha dung:
  aic start       - Khoi dong Proxy API chay ngam tren cong {get_proxy_port()}
  aic stop        - Tat Proxy API va giai phong RAM tai nguyen
  aic restart     - Khoi dong lai Proxy API Service
  aic status      - Kiem tra tinh trang he thong (Proxy, Provider, Cache)
  aic update      - Cap nhat AIC len ban moi nhat va tu dong dong bo
{test_line}
  aic login_agy   - Dang nhap Google Antigravity (Gemini Flash & Claude Sonnet)
  aic login_codex - Dang nhap OpenAI Codex (Tuy chon: Browser hoac Device Code)
  aic repair      - Tu dong sua loi, lam sach session va dong bo cache
  aic uninstall   - Khoi phuc cau hinh OpenAI goc va bao toan lich su chat de resume

{model_block}
======================================================================
"""


def print_help() -> int:
    print(build_help_text().strip())
    return 0

def main() -> int:
    if len(sys.argv) < 2:
        return print_help()

    cmd = sys.argv[1].lower().strip("-")
    if cmd in ["start"]:
        return cmd_start()
    elif cmd in ["stop"]:
        return cmd_stop()
    elif cmd in ["restart"]:
        return cmd_restart()
    elif cmd in ["status", "st"]:
        return cmd_status()
    elif cmd in ["update", "upgrade"]:
        return cmd_update()
    elif cmd in ["test", "t"]:
        return cmd_test()
    elif cmd in ["login_agy", "login-agy", "login_anti", "login-anti"]:
        return cmd_login_agy()
    elif cmd in ["login_codex", "login-codex", "login_openai", "login-openai"]:
        mode = sys.argv[2] if len(sys.argv) > 2 else None
        return cmd_login_codex(mode)
    elif cmd in ["login", "auth"]:
        sub = sys.argv[2].lower() if len(sys.argv) > 2 else "agy"
        if sub in ["agy", "anti", "antigravity"]:
            return cmd_login_agy()
        else:
            mode = sys.argv[3] if len(sys.argv) > 3 else None
            return cmd_login_codex(mode)
    elif cmd in ["repair", "repair_antigravity", "repair-antigravity", "repair_anti", "repair-anti"]:
        return cmd_repair()
    elif cmd in ["uninstall", "remove"]:
        return cmd_uninstall()
    elif cmd in ["help", "h"]:
        return print_help()
    else:
        print(f"[WARN] Lenh '{cmd}' khong hop le.")
        print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
