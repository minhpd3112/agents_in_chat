#!/usr/bin/env python3
# ==============================================================================
#  proxy_manager.py - Centralized Process & PID Manager for CLIProxyAPI
#  Python stdlib-only.
# ==============================================================================

import os
import sys
import json
import time
import socket
import signal
import shlex
import urllib.request
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from log_utils import error, info, warn  # noqa: E402


def get_state_file_path() -> Path:
    override = os.environ.get("AIC_PROXY_STATE_FILE")
    if override:
        return Path(override)
    return ROOT_DIR / ".proxy_state.json"


def get_proxy_exe_path() -> Path:
    override = os.environ.get("AIC_PROXY_EXE")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return ROOT_DIR / "cli-proxy-api.exe"
    return ROOT_DIR / "cli-proxy-api"


def get_config_path() -> Path:
    override = os.environ.get("AIC_CONFIG_FILE")
    if override:
        return Path(override)
    return ROOT_DIR / "config.yaml"


def get_proxy_port() -> int:
    env_port = os.environ.get("AIC_PORT")
    if env_port:
        try:
            return int(env_port)
        except (ValueError, TypeError):
            pass
    config_file = get_config_path()
    if config_file.exists():
        try:
            import re
            m = re.search(r"^port:\s*(\d+)", config_file.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                return int(m.group(1))
        except Exception:
            pass
    return 8090


_last_stop_backup_failed = False


def run_auth_backup_hook(action: str) -> int:
    hook_script = ROOT_DIR / "scripts" / "backup_auths.py"
    if not hook_script.exists():
        return 0
    try:
        res = subprocess.run(
            [sys.executable, "-B", str(hook_script), action],
            cwd=str(ROOT_DIR),
            env=os.environ.copy(),
            capture_output=True,
            text=True
        )
        if res.stderr:
            sys.stderr.write(res.stderr)
        if res.returncode != 0:
            warn(f"auth backup hook '{action}' exited with code {res.returncode}")
        return res.returncode
    except Exception as e:
        warn(f"auth backup hook '{action}' failed: {e}")
        return 1


def is_same_canonical_path(path_a: Path, path_b: Path) -> bool:
    try:
        p_a = Path(path_a).resolve()
        p_b = Path(path_b).resolve()
        if sys.platform == "win32":
            return os.path.normcase(str(p_a)) == os.path.normcase(str(p_b))
        return os.path.realpath(str(p_a)) == os.path.realpath(str(p_b))
    except Exception:
        return False


def parse_config_from_cmdline(cmdline: str) -> Optional[str]:
    if not cmdline:
        return None
    try:
        posix = (sys.platform != "win32")
        tokens = shlex.split(cmdline, posix=posix)
    except Exception:
        tokens = cmdline.split()

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("-config", "--config"):
            if i + 1 < len(tokens):
                return tokens[i + 1].strip("\"'")
            return None
        if token.startswith("-config=") or token.startswith("--config="):
            return token.split("=", 1)[1].strip("\"'")
        i += 1
    return None


def get_process_identity(pid: int) -> Tuple[str, Optional[str], Optional[str]]:
    if pid <= 0:
        return ("not_found", None, None)

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.windll.kernel32

        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            err = kernel32.GetLastError()
            if err == ERROR_ACCESS_DENIED:
                return ("permission_denied", None, None)
            return ("not_found", None, None)

        try:
            buf = ctypes.create_unicode_buffer(2048)
            size = wintypes.DWORD(2048)
            exe_path = None
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                exe_path = buf.value

            ft_create = wintypes.FILETIME()
            ft_exit = wintypes.FILETIME()
            ft_kernel = wintypes.FILETIME()
            ft_user = wintypes.FILETIME()
            start_id = None
            if kernel32.GetProcessTimes(
                h,
                ctypes.byref(ft_create),
                ctypes.byref(ft_exit),
                ctypes.byref(ft_kernel),
                ctypes.byref(ft_user)
            ):
                raw_int = (ft_create.dwHighDateTime << 32) + ft_create.dwLowDateTime
                start_id = str(raw_int)

            return ("ok", exe_path, start_id)
        except Exception:
            return ("error", None, None)
        finally:
            kernel32.CloseHandle(h)

    elif sys.platform.startswith("linux"):
        try:
            os.kill(pid, 0)
        except PermissionError:
            return ("permission_denied", None, None)
        except (ProcessLookupError, OSError):
            return ("not_found", None, None)

        proc_dir = Path(f"/proc/{pid}")
        if not proc_dir.exists():
            return ("not_found", None, None)

        exe_path = None
        try:
            exe_path = os.readlink(f"/proc/{pid}/exe")
        except Exception:
            pass

        start_id = None
        try:
            stat_content = (proc_dir / "stat").read_text(encoding="utf-8")
            rparen = stat_content.rfind(")")
            if rparen != -1:
                fields = stat_content[rparen + 1:].split()
                if len(fields) >= 20:
                    starttime = fields[19]
                    boot_id = ""
                    boot_file = Path("/proc/sys/kernel/random/boot_id")
                    if boot_file.exists():
                        boot_id = boot_file.read_text(encoding="utf-8").strip()
                    start_id = f"{boot_id}:{starttime}"
        except Exception:
            pass

        return ("ok", exe_path, start_id)

    else:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return ("permission_denied", None, None)
        except (ProcessLookupError, OSError):
            return ("not_found", None, None)

        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart=,command="],
                capture_output=True,
                text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                line = res.stdout.strip()
                lstart = line[:24].strip()
                cmd = line[24:].strip()
                exe_candidate = cmd.split()[0] if cmd else None
                return ("ok", exe_candidate, lstart if lstart else None)
        except Exception:
            pass
        return ("ok", None, None)


def get_process_command_line(pid: int) -> Optional[str]:
    if pid <= 0:
        return None
    if sys.platform == "win32":
        try:
            ps_cmd = f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").CommandLine'
            cmd = ["powershell", "-NoProfile", "-Command", ps_cmd]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None
    elif sys.platform.startswith("linux"):
        cmdline_file = Path(f"/proc/{pid}/cmdline")
        if cmdline_file.exists():
            try:
                raw = cmdline_file.read_bytes()
                parts = [p.decode("utf-8", "replace") for p in raw.split(bytes([0])) if p]
                return " ".join(parts)
            except Exception:
                pass
        return None
    else:
        try:
            res = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None


def is_process_alive(pid: int) -> bool:
    status, _, _ = get_process_identity(pid)
    return status in ("ok", "permission_denied")


def is_port_in_use(port: Optional[int] = None, host: str = "127.0.0.1") -> bool:
    if port is None:
        port = get_proxy_port()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return False
    except OSError:
        return True


def check_proxy_health(port: Optional[int] = None) -> Tuple[bool, list]:
    if port is None:
        port = get_proxy_port()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/models",
            headers={"User-Agent": "aic-cli"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                return True, models
    except Exception:
        pass
    return False, []


def validate_state(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    ver = data.get("state_version")
    if type(ver) is not int or isinstance(ver, bool) or ver != 1:
        return False
    pid = data.get("pid")
    if type(pid) is not int or isinstance(pid, bool) or pid <= 0:
        return False
    start_id = data.get("process_start_id")
    if not isinstance(start_id, str) or not start_id.strip():
        return False
    exe = data.get("exe")
    if not isinstance(exe, str) or not exe.strip():
        return False
    config = data.get("config")
    if not isinstance(config, str) or not config.strip():
        return False
    port = data.get("port")
    if type(port) is not int or isinstance(port, bool) or not (1 <= port <= 65535):
        return False
    return True


def read_state() -> Optional[Dict[str, Any]]:
    state_file = get_state_file_path()
    if not state_file.exists():
        return None
    try:
        raw = state_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if validate_state(data):
            return data
    except Exception:
        pass
    return None


def write_state(data: Dict[str, Any]) -> bool:
    if not validate_state(data):
        error("state validation failed; refusing to write invalid state")
        return False
    state_file = get_state_file_path()
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_name(f"{state_file.name}.tmp.{time.time_ns()}")
        content = json.dumps(data, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_file)
        return True
    except Exception as e:
        error(f"failed to write state file {state_file}: {e}")
        return False


def remove_state() -> None:
    state_file = get_state_file_path()
    try:
        if state_file.exists():
            state_file.unlink()
    except Exception:
        pass


def terminate_process(pid: int) -> bool:
    if pid <= 0:
        return True

    if sys.platform == "win32":
        import ctypes
        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                kernel32.TerminateProcess(h, 0)
            finally:
                kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return True

    for _ in range(50):
        time.sleep(0.1)
        if not is_process_alive(pid):
            return True

    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    for _ in range(10):
        time.sleep(0.1)
        if not is_process_alive(pid):
            return True

    return False


def adopt_running_proxy() -> Optional[Tuple[int, Optional[str]]]:
    expected_exe = get_proxy_exe_path()
    expected_config = get_config_path()
    port = get_proxy_port()

    if not expected_exe.exists() or not expected_config.exists():
        return None

    candidates = []

    if sys.platform == "win32":
        try:
            ps_cmd = f"Get-CimInstance Win32_Process -Filter \"Name = '{expected_exe.name}'\" | Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
            cmd = ["powershell", "-NoProfile", "-Command", ps_cmd]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                items = raw if isinstance(raw, list) else [raw]
                for item in items:
                    pid = item.get("ProcessId")
                    epath = item.get("ExecutablePath") or ""
                    cline = item.get("CommandLine") or ""
                    if pid and epath and cline:
                        if is_same_canonical_path(Path(epath), expected_exe):
                            candidates.append((int(pid), cline))
        except Exception:
            pass

    elif sys.platform.startswith("linux"):
        try:
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    pid = int(entry.name)
                    try:
                        target = os.readlink(f"/proc/{pid}/exe")
                        if is_same_canonical_path(Path(target), expected_exe):
                            cmdline_bytes = (entry / "cmdline").read_bytes()
                            parts = [p.decode("utf-8", "replace") for p in cmdline_bytes.split(bytes([0])) if p]
                            candidates.append((pid, " ".join(parts)))
                    except Exception:
                        continue
        except Exception:
            pass

    else:
        try:
            res = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        pid = int(parts[0])
                        cmd = parts[1]
                        exe_token = cmd.split()[0] if cmd else ""
                        if is_same_canonical_path(Path(exe_token), expected_exe):
                            candidates.append((pid, cmd))
        except Exception:
            pass

    for pid, cmdline in candidates:
        config_arg = parse_config_from_cmdline(cmdline)
        if not config_arg:
            continue
        if not is_same_canonical_path(Path(config_arg), expected_config):
            continue

        ok, _ = check_proxy_health(port)
        if not ok:
            continue

        status, _, start_id = get_process_identity(pid)
        if status == "ok" and start_id:
            return (pid, start_id)

    return None


def verify_managed_state() -> Tuple[str, Optional[Dict[str, Any]]]:
    state_file = get_state_file_path()
    if not state_file.exists():
        return ("no_state", None)

    state = read_state()
    if state is None:
        return ("untrusted", None)

    pid = state["pid"]
    p_status, exe_path, start_id = get_process_identity(pid)

    if p_status == "permission_denied":
        return ("permission_denied", state)

    if p_status == "not_found":
        return ("dead", state)

    if p_status != "ok" or not exe_path or not start_id:
        return ("untrusted", state)

    if str(start_id) != str(state.get("process_start_id", "")):
        return ("untrusted", state)

    if not is_same_canonical_path(Path(exe_path), get_proxy_exe_path()):
        return ("untrusted", state)

    cmdline = get_process_command_line(pid)
    if not cmdline or not cmdline.strip():
        return ("untrusted", state)

    cfg_arg = parse_config_from_cmdline(cmdline)
    if not cfg_arg or not cfg_arg.strip():
        return ("untrusted", state)

    if not is_same_canonical_path(Path(cfg_arg), get_config_path()):
        return ("untrusted", state)

    port = state.get("port", get_proxy_port())
    ok, _ = check_proxy_health(port)
    if ok:
        return ("healthy", state)
    return ("unhealthy", state)


def stop_proxy() -> int:
    global _last_stop_backup_failed
    _last_stop_backup_failed = False

    status, state = verify_managed_state()

    if status == "permission_denied":
        pid = state["pid"] if state else "unknown"
        warn(f"process PID {pid} access denied; state preserved without stopping")
        return 1

    if status == "untrusted":
        remove_state()
        warn("untrusted or foreign process state detected; cleaned state without killing foreign process")
        print("-> CLIProxyAPI hien khong chay.")
        return 0

    if status == "dead":
        remove_state()
        info("stale proxy state found and cleaned (PID not running)")
        print("-> CLIProxyAPI hien khong chay.")
        if run_auth_backup_hook("backup") != 0:
            _last_stop_backup_failed = True
            warn("Proxy stopped, but auth backup failed.")
            return 1
        return 0

    if status in ("healthy", "unhealthy"):
        pid = state["pid"]
        info(f"stopping verified proxy process (PID {pid})...")
        stopped = terminate_process(pid)
        if stopped:
            remove_state()
            print(f"-> [OFFLINE] Da tat tien trinh CLIProxyAPI thanh cong (PID {pid}).")
            if run_auth_backup_hook("backup") != 0:
                _last_stop_backup_failed = True
                warn("Proxy stopped, but auth backup failed.")
                return 1
            return 0
        else:
            error(f"failed to stop proxy process (PID {pid}) within timeout; state retained")
            return 1

    adopted = adopt_running_proxy()
    if adopted:
        pid, _ = adopted
        info(f"stopping adopted proxy process (PID {pid})...")
        stopped = terminate_process(pid)
        if stopped:
            print(f"-> [OFFLINE] Da tat tien trinh CLIProxyAPI thanh cong (PID {pid}).")
            if run_auth_backup_hook("backup") != 0:
                _last_stop_backup_failed = True
                warn("Proxy stopped, but auth backup failed.")
                return 1
            return 0
        else:
            error(f"failed to stop adopted proxy process (PID {pid})")
            return 1

    port = get_proxy_port()
    if is_port_in_use(port):
        info(f"foreign process on port {port} preserved; proxy is not running")

    print("-> CLIProxyAPI hien khong chay.")
    if run_auth_backup_hook("backup") != 0:
        _last_stop_backup_failed = True
        warn("Proxy stopped, but auth backup failed.")
        return 1
    return 0


def start_proxy() -> int:
    port = get_proxy_port()
    status, state = verify_managed_state()

    if status == "healthy":
        pid = state["pid"]
        print(f"-> [ONLINE] CLIProxyAPI dang hoat dong san sang (PID {pid}).")
        return 0

    if status == "unhealthy":
        pid = state["pid"]
        error(f"proxy process PID {pid} is running but unhealthy; please use restart")
        print("-> [WARNING] Tien trinh proxy dang chay nhung khong phan hoi. Vui long dung restart.")
        return 1

    if status == "permission_denied":
        pid = state["pid"] if state else "unknown"
        error(f"process PID {pid} access is denied; start aborted")
        return 1

    if status in ("dead", "untrusted"):
        remove_state()
        info("cleaned stale or untrusted proxy state")

    adopted = adopt_running_proxy()
    if adopted:
        pid, start_id = adopted
        written = write_state({
            "state_version": 1,
            "pid": pid,
            "exe": str(get_proxy_exe_path().resolve()),
            "config": str(get_config_path().resolve()),
            "process_start_id": start_id or "",
            "port": port
        })
        if not written:
            error(f"failed to record state for adopted PID {pid}; start aborted")
            return 1
        print(f"-> [ONLINE] CLIProxyAPI dang hoat dong san sang (adopted PID {pid}).")
        return 0

    if is_port_in_use(port):
        error(f"port conflict: port {port} is occupied by an unverified process; start aborted")
        print(f"-> [ERROR] Port {port} bi chiem boi tien trinh khac khong phai AIC Proxy. Huy bo khoi dong.")
        return 1

    run_auth_backup_hook("restore")

    proxy_exe = get_proxy_exe_path()
    config_file = get_config_path()
    if not proxy_exe.exists():
        error(f"proxy binary not found at {proxy_exe}")
        return 1
    if not config_file.exists():
        error(f"proxy config not found at {config_file}")
        return 1

    try:
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            CREATE_BREAKAWAY_FROM_JOB = 0x01000000
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            try:
                proc = subprocess.Popen(
                    [str(proxy_exe), "-config", str(config_file)],
                    cwd=str(ROOT_DIR),
                    creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL
                )
            except Exception:
                proc = subprocess.Popen(
                    [str(proxy_exe), "-config", str(config_file)],
                    cwd=str(ROOT_DIR),
                    creationflags=flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL
                )
        else:
            proc = subprocess.Popen(
                [str(proxy_exe), "-config", str(config_file)],
                cwd=str(ROOT_DIR),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
    except Exception as e:
        error(f"failed to launch proxy process: {e}")
        return 1

    p_status, _, start_id = get_process_identity(proc.pid)
    if p_status != "ok" or not start_id:
        error(f"cannot obtain process identity for spawned PID {proc.pid}; terminating child")
        terminate_process(proc.pid)
        return 1

    state_written = write_state({
        "state_version": 1,
        "pid": proc.pid,
        "exe": str(proxy_exe.resolve()),
        "config": str(config_file.resolve()),
        "process_start_id": start_id,
        "port": port
    })
    if not state_written:
        error(f"failed to write state for spawned PID {proc.pid}; terminating child process")
        terminate_process(proc.pid)
        return 1

    time.sleep(1)
    for _ in range(25):
        if not is_process_alive(proc.pid):
            remove_state()
            error(f"proxy process PID {proc.pid} exited prematurely")
            print("-> [WARNING] Da chay binary nhung dich vu proxy dung som.")
            return 1

        ok, _ = check_proxy_health(port)
        if ok:
            print(f"-> [ONLINE] CLIProxyAPI da khoi dong chay ngam thanh cong (PID {proc.pid}).")
            return 0
        time.sleep(0.5)

    remove_state()
    terminate_process(proc.pid)
    warn("binary launched but proxy service did not respond within timeout")
    print("-> [WARNING] Da chay binary nhung dich vu proxy chua phan hoi.")
    return 1


def restart_proxy() -> int:
    stop_code = stop_proxy()
    if stop_code != 0:
        if _last_stop_backup_failed:
            warn("proxy stopped, but auth backup failed; restart will not continue")
        return stop_code
    time.sleep(1)
    return start_proxy()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: proxy_manager.py start|stop|restart|status")
        return 1

    action = sys.argv[1].lower().strip("-")
    if action == "start":
        return start_proxy()
    elif action == "stop":
        return stop_proxy()
    elif action == "restart":
        return restart_proxy()
    elif action == "status":
        status, state = verify_managed_state()
        if status == "healthy":
            pid = state["pid"]
            print(f"Proxy State: PID {pid}, Status: ONLINE, Port: {state.get('port')}")
            return 0
        elif status == "unhealthy":
            pid = state["pid"]
            print(f"Proxy State: PID {pid}, Status: UNHEALTHY, Port: {state.get('port')}")
            return 1
        elif status == "permission_denied":
            pid = state["pid"] if state else "unknown"
            print(f"Proxy State: PID {pid}, Status: PERMISSION_DENIED")
            return 1
        elif status in ("dead", "untrusted"):
            print(f"Proxy State: Stale or untrusted ({status})")
            return 1
        else:
            print("Proxy State: No active proxy.")
            return 1
    else:
        error(f"unknown action '{action}'")
        return 1


if __name__ == "__main__":
    sys.exit(main())
