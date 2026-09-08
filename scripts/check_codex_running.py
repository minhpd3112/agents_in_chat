#!/usr/bin/env python3
# ==============================================================================
#  check_codex_running.py - Preflight detector for active OpenAI Codex CLI
#  Python stdlib-only.
#
#  Exit Codes:
#    0: Confirmed NOT running (safe to proceed)
#    1: Codex CLI IS currently running
#    2: Indeterminate / detection error (fail-closed)
# ==============================================================================

import os
import sys
import subprocess
from pathlib import Path
from typing import Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from log_utils import error, info  # noqa: E402


def check_codex_status() -> Tuple[int, str]:
    """
    Returns (status_code, reason).
      0: Not running
      1: Running
      2: Indeterminate
    """
    # In test mode, we only honor the mock variable AIC_CODEX_RUNNING
    # to avoid falsely detecting real user processes while testing.
    is_test_mode = os.environ.get("AIC_TEST_MODE") == "1"
    if is_test_mode:
        sim = os.environ.get("AIC_CODEX_RUNNING")
        if sim == "1":
            return (1, "Simulated Codex running (AIC_CODEX_RUNNING=1)")
        elif sim == "2":
            return (2, "Simulated indeterminate state (AIC_CODEX_RUNNING=2)")
        elif sim == "0":
            return (0, "Simulated Codex stopped (AIC_CODEX_RUNNING=0)")
        else:
            return (0, "Test mode default: safe (no AIC_CODEX_RUNNING set)")

    # Real OS detection
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        kernel32 = ctypes.windll.kernel32

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        h = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        # Invalid handle value is (HANDLE)(-1)
        if h == wintypes.HANDLE(-1).value or h == -1:
            return (2, "Failed to create process snapshot on Windows")

        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        running_pid = None

        try:
            if kernel32.Process32FirstW(h, ctypes.byref(pe)):
                while True:
                    exe_name = pe.szExeFile.lower()
                    if exe_name == "codex.exe":
                        running_pid = pe.th32ProcessID
                        break
                    if not kernel32.Process32NextW(h, ctypes.byref(pe)):
                        break
        except Exception as e:
            return (2, f"Exception while iterating processes: {e}")
        finally:
            kernel32.CloseHandle(h)

        if running_pid:
            return (1, f"Found running codex.exe (PID: {running_pid})")
        return (0, "No running codex.exe detected")

    elif sys.platform.startswith("linux"):
        proc_dir = Path("/proc")
        if not proc_dir.exists():
            return (2, "/proc directory not accessible")

        found_pid = None
        try:
            for entry in proc_dir.iterdir():
                if entry.name.isdigit():
                    pid = int(entry.name)
                    # Check comm or exe link
                    try:
                        comm_file = entry / "comm"
                        if comm_file.exists() and comm_file.read_text(encoding="utf-8").strip() == "codex":
                            found_pid = pid
                            break
                        exe_link = os.readlink(f"/proc/{pid}/exe")
                        if Path(exe_link).name == "codex":
                            found_pid = pid
                            break
                    except Exception:
                        continue
        except Exception as e:
            return (2, f"Exception scanning /proc: {e}")

        if found_pid:
            return (1, f"Found running codex process (PID: {found_pid})")
        return (0, "No running codex process detected")

    else:
        # macOS / BSD
        try:
            res = subprocess.run(["pgrep", "-x", "codex"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                pid = res.stdout.strip().splitlines()[0]
                return (1, f"Found running codex process (PID: {pid})")
            elif res.returncode == 1:
                return (0, "No running codex process detected")
            else:
                return (2, "pgrep command returned unexpected status")
        except Exception as e:
            return (2, f"Failed to execute process check: {e}")


def main() -> int:
    code, reason = check_codex_status()
    if code == 0:
        return 0
    elif code == 1:
        error(f"Codex CLI is currently running ({reason}); please close all Codex sessions before proceeding")
        return 1
    else:
        error(f"indeterminate Codex CLI process status ({reason}); aborted (fail-closed)")
        return 2


if __name__ == "__main__":
    sys.exit(main())
