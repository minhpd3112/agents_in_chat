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

VERSION = "1.0.0"

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

def check_proxy_health():
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/v1/models", headers={"User-Agent": "aic-cli"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                return True, models
    except Exception:
        pass
    return False, []

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
            "models": ["x-preview-f-free", "ox-alpha"]
        }
        zen_auth.write_text(json.dumps(auth_data, indent=2), encoding="utf-8")

def run_auth_backup_hook(action: str) -> int:
    hook_script = ROOT_DIR / "scripts" / "backup_auths.py"
    if not hook_script.exists():
        return 0
    try:
        res = subprocess.run([sys.executable, "-B", str(hook_script), action], cwd=str(ROOT_DIR))
        return res.returncode
    except Exception as e:
        warn(f"auth backup hook '{action}' failed: {e}")
        return 0

def cmd_start() -> int:
    ensure_default_compat_auth()
    # [SAFETY] Auto-Recovery: phuc hoi token hong tu auths_backup/ truoc khi khoi dong proxy.
    run_auth_backup_hook("restore")
    online, _ = check_proxy_health()
    if online:
        info("proxy service already running")
        return 0

    info("starting proxy service...")
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "start.ps1"
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)])
        if res.returncode != 0:
            return res.returncode
    else:
        sh_script = ROOT_DIR / "start.sh"
        res = subprocess.run(["bash", str(sh_script)])
        if res.returncode != 0:
            return res.returncode

    import time
    for _ in range(5):
        time.sleep(1)
        ok, models = check_proxy_health()
        if ok:
            info(f"proxy started ({len(models)} models online)")
            return 0
    warn("binary launched but proxy not responding")
    return 1


def cmd_stop() -> int:
    info("stopping proxy service...")
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "stop.ps1"
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)])
    else:
        sh_script = ROOT_DIR / "stop.sh"
        res = subprocess.run(["bash", str(sh_script)])
    # [SAFETY] Auto-Backup: snapshot token moi nhat sau khi proxy da dung han.
    run_auth_backup_hook("backup")
    return 0 if res.returncode == 0 else res.returncode

def cmd_restart() -> int:
    stop_code = cmd_stop()
    if stop_code != 0:
        return stop_code
    import time
    time.sleep(1)
    return cmd_start()

def cmd_status() -> int:
    print("=" * 65)
    print(f"  AGENTS IN CHAT (AIC) SYSTEM STATUS  |  v{VERSION}")
    print("=" * 65)

    # Auth count is needed by both the Provider label and section 4
    auths_dir = ROOT_DIR / "auths"
    auth_count = len(list(auths_dir.glob("*.json"))) if auths_dir.exists() else 0

    # 1. Proxy
    online, models = check_proxy_health()
    if online:
        models_str = ", ".join(models)
        print(f"[OK] Proxy Service (127.0.0.1:8080) : ONLINE [200 OK]")
        print(f"     -> Models Online ({len(models)}): {models_str}")
    else:
        print(f"[OFFLINE] Proxy Service (127.0.0.1:8080) : OFFLINE")

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
    print("=" * 65)
    return 0 if online else 1

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
  aic start       - Khoi dong Proxy API chay ngam tren cong 8080
  aic stop        - Tat Proxy API va giai phong RAM tai nguyen
  aic restart     - Khoi dong lai Proxy API Service
  aic status      - Kiem tra tinh trang he thong (Proxy, Provider, Cache)
{test_line}
  aic login_agy   - Dang nhap Google Antigravity (Gemini Flash & Claude Sonnet/Opus)
  aic login_codex - Dang nhap OpenAI Codex (Tuy chon: Browser hoac Device Code)
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
