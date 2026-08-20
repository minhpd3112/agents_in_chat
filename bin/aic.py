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
    # If aic.py is in bin/ subfolder, the project root is parent directory
    if (script_dir.parent / "config.yaml").exists() or (script_dir.parent / "install.ps1").exists():
        return script_dir.parent
    # If aic.py is placed directly in root directory
    if (script_dir / "config.yaml").exists() or (script_dir / "install.ps1").exists():
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

def cmd_start():
    online, _ = check_proxy_health()
    if online:
        print("[AIC] Proxy API Service da dang chay san tren port 8080.")
        return

    print("[AIC] Dang khoi dong Proxy API Service chay ngam 100% (an hoan toan)...")
    if sys.platform == "win32":
        proxy_exe = ROOT_DIR / "cli-proxy-api.exe"
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            [str(proxy_exe)],
            cwd=str(ROOT_DIR),
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )
    else:
        sh_script = ROOT_DIR / "start.sh"
        subprocess.run(["bash", str(sh_script)])
        
    import time
    for _ in range(5):
        time.sleep(1)
        ok, models = check_proxy_health()
        if ok:
            print(f"[OK] CLIProxyAPI da khoi dong thanh cong tai http://127.0.0.1:8080 ({len(models)} models online)")
            return
    print("[WARNING] Da chay binary nhung chua nhan phan hoi port 8080.")


def cmd_stop():
    print("[AIC] Dang tat Proxy API Service...")
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "stop.ps1"
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)])
    else:
        sh_script = ROOT_DIR / "stop.sh"
        subprocess.run(["bash", str(sh_script)])

def cmd_restart():
    cmd_stop()
    import time
    time.sleep(1)
    cmd_start()

def cmd_status():
    print("=" * 65)
    print(f"  AGENTS IN CHAT (AIC) SYSTEM STATUS  |  v{VERSION}")
    print("=" * 65)
    
    # 1. Proxy
    online, models = check_proxy_health()
    if online:
        print(f"[OK] Proxy Service (127.0.0.1:8080) : ONLINE [200 OK]")
        print(f"     -> Models Online ({len(models)}): {', '.join(models)}")
    else:
        print(f"[OFFLINE] Proxy Service (127.0.0.1:8080) : OFFLINE")

    # 2. Config Provider
    config_file = CODEX_DIR / "config.toml"
    provider_str = "Chua ro"
    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8")
            if 'model_provider = "custom"' in content:
                provider_str = "custom (Agents Quota Pool - 10 OAuth Accounts)"
            elif 'model_provider = "openai"' in content:
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

def cmd_test():
    test_runner = ROOT_DIR / "tests" / "run_tests.py"
    if not test_runner.exists():
        print(f"[ERROR] Khong tim thay bo test tai {test_runner}!")
        return 1
    return subprocess.run([sys.executable, str(test_runner)]).returncode

def cmd_sync(target="custom"):
    sync_script = ROOT_DIR / "scripts" / "sync_sessions.py"
    if sync_script.exists():
        subprocess.run([sys.executable, str(sync_script), target])
    else:
        print(f"[ERROR] Khong tim thay script dong bo tai {sync_script}")

def cmd_uninstall():
    if sys.platform == "win32":
        ps_script = ROOT_DIR / "uninstall.ps1"
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)])
    else:
        sh_script = ROOT_DIR / "uninstall.sh"
        subprocess.run(["bash", str(sh_script)])

def cmd_login_agy():
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

def cmd_login_codex(mode=None):
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
        print("  [2] Device Code Flow   (Nhap ma xac thuc tren auth0.openai.com/activate)")
        print("-" * 68)
        try:
            choice = input("Lua chon cua ban [1/2] (Mac dinh: 1): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[AIC] Da huy thao tac dang nhap.")
            return 0
        except Exception:
            choice = "1"
        mode = "device" if choice == "2" else "browser"

    mode = mode.lower()
    if mode in ["device", "code", "device-code", "device_code", "2"]:
        print("\n[AIC] Dang khoi dong Device Code Flow...")
        print("-> Vui long truy cap https://auth0.openai.com/activate va nhap ma:")
        print("-" * 68)
        args = [str(proxy_exe), "-codex-device-login"]
    else:
        print("\n[AIC] Dang khoi dong Browser OAuth Flow...")
        print("-> Vui long click hoac copy duong link duoi day dan vao trinh duyet:")
        print("-" * 68)
        args = [str(proxy_exe), "-codex-login", "-no-browser"]

    return subprocess.run(args, cwd=str(ROOT_DIR)).returncode


def print_help():
    print("""
======================================================================
               AGENTS IN CHAT (AIC) CLI MANAGER v1.0.0
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
  aic sync        - Dong bo lich su chat giua custom va openai (aic sync [custom|openai])
  aic uninstall   - Factory Reset 100% ve nguyen ban OpenAI Codex CLI

Danh sach 6 model ho tro trong menu /model cua Codex CLI:
  1. gemini-3.7-flash            (High Thinking, Function Calling)
  2. claude-sonnet-4.6-thinking  (Deep Reasoning, Tool Calling)
  3. claude-opus-4.6-thinking    (Ultra Thinking Architecture)
  4. gpt-5.6-sol                 (Flagship Frontier)
  5. gpt-5.6-terra               (Balanced Agentic)
  6. gpt-5.6-luna                (Fast & Lightweight)
======================================================================
""")

def main():
    if len(sys.argv) < 2:
        print_help()
        return

    cmd = sys.argv[1].lower().strip("-")
    if cmd in ["start"]:
        cmd_start()
    elif cmd in ["stop"]:
        cmd_stop()
    elif cmd in ["restart"]:
        cmd_restart()
    elif cmd in ["status", "st"]:
        cmd_status()
    elif cmd in ["test", "t"]:
        cmd_test()
    elif cmd in ["login_agy", "login-agy", "login_anti", "login-anti"]:
        cmd_login_agy()
    elif cmd in ["login_codex", "login-codex", "login_openai", "login-openai"]:
        mode = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_login_codex(mode)
    elif cmd in ["login", "auth"]:
        sub = sys.argv[2].lower() if len(sys.argv) > 2 else "agy"
        if sub in ["agy", "anti", "antigravity"]:
            cmd_login_agy()
        else:
            mode = sys.argv[3] if len(sys.argv) > 3 else None
            cmd_login_codex(mode)
    elif cmd in ["sync"]:
        target = sys.argv[2] if len(sys.argv) > 2 else "custom"
        cmd_sync(target)
    elif cmd in ["uninstall", "remove"]:
        cmd_uninstall()
    elif cmd in ["help", "h"]:
        print_help()
    else:
        print(f"[WARN] Lenh '{cmd}' khong hop le.")
        print_help()


if __name__ == "__main__":
    main()
