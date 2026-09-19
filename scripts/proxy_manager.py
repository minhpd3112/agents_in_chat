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


def get_backend_config_path() -> Path:
    override = os.environ.get("AIC_BACKEND_CONFIG_FILE")
    if override:
        return Path(override)
    return ROOT_DIR / ".backend_config.yaml"


def get_sanitizer_script_path() -> Path:
    override = os.environ.get("AIC_SANITIZER_SCRIPT")
    if override:
        return Path(override)
    return ROOT_DIR / "scripts" / "request_sanitizer.py"


def get_backend_port() -> int:
    env_port = os.environ.get("AIC_BACKEND_PORT")
    if env_port:
        try:
            return int(env_port)
        except (ValueError, TypeError):
            pass
    public_port = get_proxy_port()
    candidate = public_port + 5
    if candidate <= 65535 and not is_port_in_use(candidate):
        return candidate
    for p in range(public_port + 1, min(65535, public_port + 50)):
        if p != public_port and not is_port_in_use(p):
            return p
    return 8095


def generate_backend_config(source_config: Path, target_config: Path, backend_port: int) -> bool:
    try:
        raw_text = source_config.read_text(encoding="utf-8")
        import re
        if re.search(r"^port:\s*\d+", raw_text, flags=re.MULTILINE):
            new_text = re.sub(r"^port:\s*\d+", f"port: {backend_port}", raw_text, flags=re.MULTILINE)
        else:
            new_text = f"port: {backend_port}\n" + raw_text
        target_config.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_config.with_name(f"{target_config.name}.tmp.{time.time_ns()}")
        tmp.write_text(new_text, encoding="utf-8", newline="\n")
        os.replace(tmp, target_config)
        return True
    except Exception as e:
        error(f"failed to generate backend config: {e}")
        return False


def clean_backend_config() -> None:
    try:
        b_cfg = get_backend_config_path()
        if b_cfg.exists():
            b_cfg.unlink()
    except Exception:
        pass


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
    if type(ver) is not int or isinstance(ver, bool):
        return False

    if ver == 1:
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

    elif ver == 2:
        sanitizer_pid = data.get("sanitizer_pid")
        if type(sanitizer_pid) is not int or isinstance(sanitizer_pid, bool) or sanitizer_pid <= 0:
            return False
        backend_pid = data.get("backend_pid")
        if type(backend_pid) is not int or isinstance(backend_pid, bool) or backend_pid <= 0:
            return False
        pid = data.get("pid")
        if type(pid) is not int or isinstance(pid, bool) or pid <= 0:
            return False
        start_id = data.get("process_start_id")
        if not isinstance(start_id, str) or not start_id.strip():
            return False
        backend_start_id = data.get("backend_start_id")
        if not isinstance(backend_start_id, str) or not backend_start_id.strip():
            return False
        exe = data.get("exe")
        if not isinstance(exe, str) or not exe.strip():
            return False
        port = data.get("port")
        if type(port) is not int or isinstance(port, bool) or not (1 <= port <= 65535):
            return False
        backend_port = data.get("backend_port")
        if type(backend_port) is not int or isinstance(backend_port, bool) or not (1 <= backend_port <= 65535):
            return False
        return True

    return False


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

    ver = state.get("state_version", 1)
    port = state.get("port", get_proxy_port())

    if ver == 2:
        sanitizer_pid = state.get("sanitizer_pid", 0)
        backend_pid = state.get("backend_pid", 0)

        s_status, s_exe, s_start_id = get_process_identity(sanitizer_pid)
        b_status, b_exe, b_start_id = get_process_identity(backend_pid)

        if s_status == "permission_denied" or b_status == "permission_denied":
            return ("permission_denied", state)

        if s_status == "not_found" or b_status == "not_found":
            return ("dead", state)

        if s_status != "ok" or b_status != "ok" or not s_start_id or not b_start_id:
            return ("untrusted", state)

        if str(s_start_id) != str(state.get("process_start_id", "")) or str(b_start_id) != str(state.get("backend_start_id", "")):
            return ("untrusted", state)

        if not is_same_canonical_path(Path(b_exe), get_proxy_exe_path()):
            return ("untrusted", state)

        ok, _ = check_proxy_health(port)
        if ok:
            return ("healthy", state)
        return ("unhealthy", state)

    else:
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

        ok, _ = check_proxy_health(port)
        if ok:
            return ("healthy", state)
        return ("unhealthy", state)


def stop_proxy() -> int:
    global _last_stop_backup_failed
    _last_stop_backup_failed = False

    status, state = verify_managed_state()

    if status == "permission_denied":
        pid = state.get("sanitizer_pid", state.get("pid", "unknown")) if state else "unknown"
        warn(f"process PID {pid} access denied; state preserved without stopping")
        return 1

    if status == "untrusted":
        remove_state()
        clean_backend_config()
        warn("untrusted or foreign process state detected; cleaned state without killing foreign process")
        print("-> CLIProxyAPI hien khong chay.")
        return 0

    if status == "dead":
        remove_state()
        clean_backend_config()
        info("stale proxy state found and cleaned (PID not running)")
        print("-> CLIProxyAPI hien khong chay.")
        if run_auth_backup_hook("backup") != 0:
            _last_stop_backup_failed = True
            warn("Proxy stopped, but auth backup failed.")
            return 1
        return 0

    if status in ("healthy", "unhealthy"):
        if state and state.get("state_version") == 2:
            s_pid = state["sanitizer_pid"]
            b_pid = state["backend_pid"]
            info(f"stopping verified processes (Sanitizer PID {s_pid}, Backend PID {b_pid})...")
            stopped_s = terminate_process(s_pid)
            stopped_b = terminate_process(b_pid)
            clean_backend_config()
            remove_state()
            if stopped_s and stopped_b:
                print(f"-> [OFFLINE] Da tat tien trinh AIC Proxy & Request Sanitizer thanh cong (PID {s_pid}, {b_pid}).")
            else:
                warn(f"one or more processes could not be stopped cleanly (Sanitizer: {stopped_s}, Backend: {stopped_b})")
            if run_auth_backup_hook("backup") != 0:
                _last_stop_backup_failed = True
                warn("Proxy stopped, but auth backup failed.")
                return 1
            return 0 if (stopped_s and stopped_b) else 1
        else:
            pid = state["pid"]
            info(f"stopping verified proxy process (PID {pid})...")
            stopped = terminate_process(pid)
            clean_backend_config()
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
        clean_backend_config()
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

    clean_backend_config()
    print("-> CLIProxyAPI hien khong chay.")
    if run_auth_backup_hook("backup") != 0:
        _last_stop_backup_failed = True
        warn("Proxy stopped, but auth backup failed.")
        return 1
    return 0


def spawn_daemon(cmd_list: list, cwd: Path) -> Optional[int]:
    """Spawn a long-running background daemon process that survives parent exit on Windows and Unix."""
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd),
                creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
            return proc.pid
        except Exception:
            pass

        # Fallback to WMI/CIM Win32_Process.Create to escape nested job objects
        try:
            cmd_str = subprocess.list2cmdline([str(x) for x in cmd_list])
            ps_escaped_cmd = cmd_str.replace("`", "``").replace('"', '`"')
            ps_escaped_cwd = str(cwd.resolve()).replace("`", "``").replace('"', '`"')
            ps_script = (
                f"(Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                f"-Arguments @{{CommandLine = \"{ps_escaped_cmd}\"; CurrentDirectory = \"{ps_escaped_cwd}\"}}).ProcessId"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10.0
            )
            if res.returncode == 0 and res.stdout.strip().isdigit():
                return int(res.stdout.strip())
        except Exception:
            pass

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd),
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
            return proc.pid
        except Exception:
            return None
    else:
        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
            return proc.pid
        except Exception:
            return None


def start_proxy() -> int:
    port = get_proxy_port()
    backend_port = get_backend_port()
    status, state = verify_managed_state()

    if status == "healthy":
        if state and state.get("state_version") == 2:
            s_pid = state.get("sanitizer_pid")
            b_pid = state.get("backend_pid")
            print(f"-> [ONLINE] AIC Proxy & Request Sanitizer dang hoat dong san sang (Sanitizer PID {s_pid}, Backend PID {b_pid}).")
        else:
            pid = state["pid"]
            print(f"-> [ONLINE] CLIProxyAPI dang hoat dong san sang (PID {pid}).")
        return 0

    if status == "unhealthy":
        pid = state.get("sanitizer_pid", state.get("pid")) if state else "unknown"
        error(f"proxy process PID {pid} is running but unhealthy; please use restart")
        print("-> [WARNING] Tien trinh proxy dang chay nhung khong phan hoi. Vui long dung restart.")
        return 1

    if status == "permission_denied":
        pid = state.get("sanitizer_pid", state.get("pid", "unknown")) if state else "unknown"
        error(f"process PID {pid} access is denied; start aborted")
        return 1

    if status in ("dead", "untrusted"):
        remove_state()
        clean_backend_config()
        info("cleaned stale or untrusted proxy state")

    if is_port_in_use(port):
        error(f"port conflict: public port {port} is occupied by an unverified process; start aborted")
        print(f"-> [ERROR] Port {port} bi chiem boi tien trinh khac khong phai AIC Proxy. Huy bo khoi dong.")
        return 1

    if is_port_in_use(backend_port):
        error(f"port conflict: backend port {backend_port} is occupied; start aborted")
        print(f"-> [ERROR] Backend port {backend_port} bi chiem boi tien trinh khac. Huy bo khoi dong.")
        return 1

    run_auth_backup_hook("restore")

    proxy_exe = get_proxy_exe_path()
    config_file = get_config_path()
    backend_config = get_backend_config_path()
    sanitizer_script = get_sanitizer_script_path()

    if not proxy_exe.exists():
        error(f"proxy binary not found at {proxy_exe}")
        return 1
    if not config_file.exists():
        error(f"proxy config not found at {config_file}")
        return 1
    if not sanitizer_script.exists():
        error(f"sanitizer script not found at {sanitizer_script}")
        return 1

    if not generate_backend_config(config_file, backend_config, backend_port):
        error("failed to generate backend configuration; start aborted")
        return 1

    # 1. Launch Backend (cli-proxy-api) on internal port
    backend_cmd = [str(proxy_exe), "-config", str(backend_config)]
    backend_pid = spawn_daemon(backend_cmd, ROOT_DIR)
    if not backend_pid:
        error("failed to launch backend proxy process")
        clean_backend_config()
        return 1

    b_status, _, b_start_id = get_process_identity(backend_pid)
    if b_status != "ok" or not b_start_id:
        error(f"cannot obtain identity for backend PID {backend_pid}; terminating")
        terminate_process(backend_pid)
        clean_backend_config()
        return 1

    # Wait for backend to be healthy on backend_port
    backend_healthy = False
    for _ in range(30):
        if not is_process_alive(backend_pid):
            error(f"backend proxy PID {backend_pid} exited prematurely")
            clean_backend_config()
            return 1
        ok, _ = check_proxy_health(backend_port)
        if ok:
            backend_healthy = True
            break
        time.sleep(0.5)

    if not backend_healthy:
        error(f"backend proxy PID {backend_pid} did not become healthy on port {backend_port}")
        terminate_process(backend_pid)
        clean_backend_config()
        return 1

    # 2. Launch Sanitizer Proxy on public port
    sanitizer_cmd = [
        sys.executable,
        "-B",
        str(sanitizer_script),
        "--host", "127.0.0.1",
        "--port", str(port),
        "--backend-host", "127.0.0.1",
        "--backend-port", str(backend_port),
    ]
    sanitizer_pid = spawn_daemon(sanitizer_cmd, ROOT_DIR)
    if not sanitizer_pid:
        error("failed to launch sanitizer proxy process")
        terminate_process(backend_pid)
        clean_backend_config()
        return 1

    s_status, _, s_start_id = get_process_identity(sanitizer_pid)
    if s_status != "ok" or not s_start_id:
        error(f"cannot obtain identity for sanitizer PID {sanitizer_pid}; terminating both")
        terminate_process(sanitizer_pid)
        terminate_process(backend_pid)
        clean_backend_config()
        return 1

    # 3. Write state file version 2
    state_written = write_state({
        "state_version": 2,
        "pid": sanitizer_pid,
        "sanitizer_pid": sanitizer_pid,
        "backend_pid": backend_pid,
        "exe": str(proxy_exe.resolve()),
        "config": str(config_file.resolve()),
        "backend_config": str(backend_config.resolve()),
        "process_start_id": s_start_id,
        "backend_start_id": b_start_id,
        "port": port,
        "backend_port": backend_port,
    })
    if not state_written:
        error("failed to record proxy state; terminating child processes")
        terminate_process(sanitizer_pid)
        terminate_process(backend_pid)
        clean_backend_config()
        return 1

    # 4. Wait for full pipeline health check on public port
    pipeline_healthy = False
    for _ in range(30):
        if not is_process_alive(sanitizer_pid) or not is_process_alive(backend_pid):
            remove_state()
            clean_backend_config()
            terminate_process(sanitizer_pid)
            terminate_process(backend_pid)
            error("proxy or sanitizer process exited prematurely")
            return 1

        ok, _ = check_proxy_health(port)
        if ok:
            pipeline_healthy = True
            break
        time.sleep(0.5)

    if pipeline_healthy:
        print(f"-> [ONLINE] AIC Proxy & Request Sanitizer da khoi dong thanh cong (PID {sanitizer_pid}, {backend_pid} | Ports {port} -> {backend_port}).")
        return 0

    remove_state()
    clean_backend_config()
    terminate_process(sanitizer_pid)
    terminate_process(backend_pid)
    warn("proxy pipeline did not respond within timeout")
    print("-> [WARNING] Da chay dich vu nhung he thong proxy chua phan hoi.")
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
            if state and state.get("state_version") == 2:
                s_pid = state["sanitizer_pid"]
                b_pid = state["backend_pid"]
                print(f"Proxy State: Sanitizer PID {s_pid} (Port {state.get('port')}), Backend PID {b_pid} (Port {state.get('backend_port')}), Status: ONLINE")
            else:
                pid = state["pid"]
                print(f"Proxy State: PID {pid}, Status: ONLINE, Port: {state.get('port')}")
            return 0
        elif status == "unhealthy":
            pid = state.get("sanitizer_pid", state.get("pid")) if state else "unknown"
            print(f"Proxy State: PID {pid}, Status: UNHEALTHY, Port: {state.get('port') if state else 'unknown'}")
            return 1
        elif status == "permission_denied":
            pid = state.get("sanitizer_pid", state.get("pid", "unknown")) if state else "unknown"
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
