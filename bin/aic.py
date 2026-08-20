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

def cmd_start() -> int:
    online, _ = check_proxy_health()
    if online:
        print("[AIC] Proxy API Service da dang hoat dong san sang.")
        return 0

    print("[AIC] Dang khoi dong Proxy API Service chay ngam 100% (an hoan toan)...")
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
            print(f"[OK] CLIProxyAPI da khoi dong thanh cong ({len(models)} models online)")
            return 0
    print("[WARNING] Da chay binary nhung dich vu proxy chua phan hoi.")
    return 1


def cmd_stop() -> int:
    print("[AIC] Dang tat Proxy API Service...")
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "stop.ps1"
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)])
        return res.returncode
    else:
        sh_script = ROOT_DIR / "stop.sh"
        res = subprocess.run(["bash", str(sh_script)])
        return res.returncode

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
                provider_str = "custom (Agents Quota Pool - 10 OAuth Accounts)"
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
    auths_dir = ROOT_DIR / "auths"
    auth_count = len(list(auths_dir.glob("*.json"))) if auths_dir.exists() else 0
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
    return subprocess.run(args, cwd=str(ROOT_DIR)).returncode

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
            choice = input("Lua chon cua ban [1/2] (Mac dinh: 1): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[AIC] Da huy thao tac dang nhap.")
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

    return subprocess.run(args, cwd=str(ROOT_DIR)).returncode


HELP_TEXT = f"""
======================================================================
               AGENTS IN CHAT (AIC) CLI MANAGER v{VERSION}
======================================================================
Su dung: aic <lenh> [tuy chon]

Cac lenh kha dung:
  aic start       - Khoi dong Proxy API chay ngam tren cong 8080
  aic stop        - Tat Proxy API va giai phong RAM tai nguyen
  aic restart     - Khoi dong lai Proxy API Service
  aic status      - Kiem tra tinh trang he thong (Proxy, Provider, Cache)
  aic test        - Chay bo kiem thu tu dong 7/7 test suites
  aic login_agy   - Dang nhap Google Antigravity (Gemini Flash & Claude Sonnet/Opus)
  aic login_codex - Dang nhap OpenAI Codex (Tuy chon: Browser hoac Device Code)
  aic uninstall   - Khoi phuc cau hinh OpenAI goc va bao toan lich su chat de resume

Danh sach 6 model ho tro trong menu /model cua Codex CLI:
  1. gemini-3.7-flash            (High Thinking, Function Calling)
  2. claude-sonnet-4.6-thinking  (Deep Reasoning, Tool Calling)
  3. claude-opus-4.6-thinking    (Ultra Thinking Architecture)
  4. gpt-5.6-sol                 (Flagship Frontier)
  5. gpt-5.6-terra               (Balanced Agentic)
  6. gpt-5.6-luna                (Fast & Lightweight)
======================================================================
"""

def print_help() -> int:
    print(HELP_TEXT.strip())
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
