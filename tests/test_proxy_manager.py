#!/usr/bin/env python3
# ==============================================================================
#  test_proxy_manager.py - Unit and Lifecycle Tests for proxy_manager.py
#  State Version 1 & 2, Partial Failure, Survivor Prevention & Process Hiding.
#  Python stdlib-only.
# ==============================================================================

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import proxy_manager


class TestProxyManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aic_proxy_test_")
        self.state_file = Path(self.temp_dir) / ".proxy_state.json"
        self.backend_config_file = Path(self.temp_dir) / ".backend_config.yaml"
        self.auths_dir = Path(self.temp_dir) / "auths"
        self.auths_backup_dir = Path(self.temp_dir) / "auths_backup"
        self.auths_dir.mkdir(parents=True, exist_ok=True)
        self.auths_backup_dir.mkdir(parents=True, exist_ok=True)

        self.orig_env = os.environ.copy()
        os.environ["AIC_PROXY_STATE_FILE"] = str(self.state_file)
        os.environ["AIC_BACKEND_CONFIG_FILE"] = str(self.backend_config_file)
        os.environ["AIC_AUTHS_DIR"] = str(self.auths_dir)
        os.environ["AIC_AUTHS_BACKUP_DIR"] = str(self.auths_backup_dir)
        os.environ["AIC_TEST_MODE"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. State File Atomic Write & Schema Validation
    # --------------------------------------------------------------------------
    def test_state_file_atomic_write_and_strict_schema(self):
        # Version 1 schema
        data_v1 = {
            "state_version": 1,
            "pid": 12345,
            "exe": "C:\\test\\cli-proxy-api.exe",
            "config": "C:\\test\\config.yaml",
            "process_start_id": "134333332327778296",
            "port": 8080,
        }
        self.assertTrue(proxy_manager.write_state(data_v1))
        self.assertTrue(self.state_file.exists())

        raw_bytes = self.state_file.read_bytes()
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"), "State file must not have UTF-8 BOM")
        self.assertEqual(proxy_manager.read_state(), data_v1)

        # Version 2 schema
        data_v2 = {
            "state_version": 2,
            "pid": 10001,
            "sanitizer_pid": 10001,
            "backend_pid": 10002,
            "exe": "C:\\test\\cli-proxy-api.exe",
            "config": "C:\\test\\config.yaml",
            "backend_config": "C:\\test\\.backend_config.yaml",
            "process_start_id": "sid_sanitizer",
            "backend_start_id": "sid_backend",
            "port": 8090,
            "backend_port": 8095,
        }
        self.assertTrue(proxy_manager.write_state(data_v2))
        self.assertEqual(proxy_manager.read_state(), data_v2)

        # Schema rejections
        invalid_pid = data_v2.copy()
        invalid_pid["sanitizer_pid"] = True
        self.assertFalse(proxy_manager.validate_state(invalid_pid))

        invalid_port = data_v2.copy()
        invalid_port["backend_port"] = "8095"
        self.assertFalse(proxy_manager.validate_state(invalid_port))

        invalid_sid = data_v2.copy()
        invalid_sid["backend_start_id"] = "   "
        self.assertFalse(proxy_manager.validate_state(invalid_sid))

        self.state_file.write_text("{ corrupt json", encoding="utf-8")
        self.assertIsNone(proxy_manager.read_state())

        proxy_manager.remove_state()
        self.assertFalse(self.state_file.exists())

    # --------------------------------------------------------------------------
    # 2. State Version 2: Both Alive & Healthy
    # --------------------------------------------------------------------------
    def test_v2_both_alive_and_healthy_stop_terminates_both(self):
        s_pid = 11111
        b_pid = 11112
        state_data = {
            "state_version": 2,
            "pid": s_pid,
            "sanitizer_pid": s_pid,
            "backend_pid": b_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_s_1",
            "backend_start_id": "sid_b_1",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        def mock_identity(pid):
            if pid == s_pid:
                return ("ok", sys.executable, "sid_s_1")
            elif pid == b_pid:
                return ("ok", str(proxy_manager.get_proxy_exe_path().resolve()), "sid_b_1")
            return ("not_found", None, None)

        def mock_cmdline(pid):
            if pid == s_pid:
                return "python.exe -B scripts/request_sanitizer.py --port 8090"
            elif pid == b_pid:
                return f'cli-proxy-api.exe -config "{self.backend_config_file}"'
            return None

        # Process alive tracker
        alive_procs = {s_pid: True, b_pid: True}

        def mock_terminate(pid):
            alive_procs[pid] = False
            return True

        with patch("proxy_manager.get_process_identity", side_effect=mock_identity):
            with patch("proxy_manager.get_process_command_line", side_effect=mock_cmdline):
                with patch("proxy_manager.check_proxy_health", return_value=(True, ["m1"])):
                    status, verified_state = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "healthy")

                with patch("proxy_manager.is_process_alive", side_effect=lambda p: alive_procs.get(p, False)):
                    with patch("proxy_manager.terminate_process", side_effect=mock_terminate) as mock_kill:
                        with patch("proxy_manager.run_auth_backup_hook", return_value=0) as mock_backup:
                            rc = proxy_manager.stop_proxy()
                            self.assertEqual(rc, 0)
                            self.assertFalse(self.state_file.exists())
                            self.assertFalse(self.backend_config_file.exists(), "Backend config must be cleaned after stop")
                            self.assertEqual(mock_kill.call_count, 2)
                            mock_backup.assert_called_with("backup")

    # --------------------------------------------------------------------------
    # 3. State Version 2: Sanitizer Dead, Backend Alive (Partial Failure)
    # --------------------------------------------------------------------------
    def test_v2_sanitizer_dead_backend_alive_partial_stopped(self):
        s_pid = 22221
        b_pid = 22222
        state_data = {
            "state_version": 2,
            "pid": s_pid,
            "sanitizer_pid": s_pid,
            "backend_pid": b_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_s_2",
            "backend_start_id": "sid_b_2",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        # Sanitizer is dead (not_found), Backend is alive (ok)
        def mock_identity(pid):
            if pid == s_pid:
                return ("not_found", None, None)
            elif pid == b_pid:
                return ("ok", str(proxy_manager.get_proxy_exe_path().resolve()), "sid_b_2")
            return ("not_found", None, None)

        def mock_cmdline(pid):
            if pid == b_pid:
                return f'cli-proxy-api.exe -config "{self.backend_config_file}"'
            return None

        alive_procs = {s_pid: False, b_pid: True}

        def mock_terminate(pid):
            alive_procs[pid] = False
            return True

        with patch("proxy_manager.get_process_identity", side_effect=mock_identity):
            with patch("proxy_manager.get_process_command_line", side_effect=mock_cmdline):
                status, _ = proxy_manager.verify_managed_state()
                self.assertEqual(status, "partial", "Must detect partial state when sanitizer dead and backend alive")

                with patch("proxy_manager.is_process_alive", side_effect=lambda p: alive_procs.get(p, False)):
                    with patch("proxy_manager.terminate_process", side_effect=mock_terminate) as mock_kill:
                        with patch("proxy_manager.run_auth_backup_hook", return_value=0) as mock_backup:
                            rc = proxy_manager.stop_proxy()
                            self.assertEqual(rc, 0)
                            mock_kill.assert_called_once_with(b_pid)
                            self.assertFalse(self.state_file.exists())
                            self.assertFalse(self.backend_config_file.exists())
                            mock_backup.assert_called_with("backup")

    # --------------------------------------------------------------------------
    # 4. State Version 2: Backend Dead, Sanitizer Alive (Partial Failure)
    # --------------------------------------------------------------------------
    def test_v2_backend_dead_sanitizer_alive_partial_stopped(self):
        s_pid = 33331
        b_pid = 33332
        state_data = {
            "state_version": 2,
            "pid": s_pid,
            "sanitizer_pid": s_pid,
            "backend_pid": b_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_s_3",
            "backend_start_id": "sid_b_3",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        # Backend is dead (not_found), Sanitizer is alive (ok)
        def mock_identity(pid):
            if pid == s_pid:
                return ("ok", sys.executable, "sid_s_3")
            elif pid == b_pid:
                return ("not_found", None, None)
            return ("not_found", None, None)

        def mock_cmdline(pid):
            if pid == s_pid:
                return "python.exe -B scripts/request_sanitizer.py --port 8090"
            return None

        alive_procs = {s_pid: True, b_pid: False}

        def mock_terminate(pid):
            alive_procs[pid] = False
            return True

        with patch("proxy_manager.get_process_identity", side_effect=mock_identity):
            with patch("proxy_manager.get_process_command_line", side_effect=mock_cmdline):
                status, _ = proxy_manager.verify_managed_state()
                self.assertEqual(status, "partial", "Must detect partial state when backend dead and sanitizer alive")

                with patch("proxy_manager.is_process_alive", side_effect=lambda p: alive_procs.get(p, False)):
                    with patch("proxy_manager.terminate_process", side_effect=mock_terminate) as mock_kill:
                        with patch("proxy_manager.run_auth_backup_hook", return_value=0) as mock_backup:
                            rc = proxy_manager.stop_proxy()
                            self.assertEqual(rc, 0)
                            mock_kill.assert_called_once_with(s_pid)
                            self.assertFalse(self.state_file.exists())
                            self.assertFalse(self.backend_config_file.exists())
                            mock_backup.assert_called_with("backup")

    # --------------------------------------------------------------------------
    # 5. State Version 2: Both Dead
    # --------------------------------------------------------------------------
    def test_v2_both_dead_cleans_state_and_backend_config(self):
        state_data = {
            "state_version": 2,
            "pid": 44441,
            "sanitizer_pid": 44441,
            "backend_pid": 44442,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_s_4",
            "backend_start_id": "sid_b_4",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        with patch("proxy_manager.get_process_identity", return_value=("not_found", None, None)):
            status, _ = proxy_manager.verify_managed_state()
            self.assertEqual(status, "dead")

            with patch("proxy_manager.terminate_process") as mock_kill:
                with patch("proxy_manager.run_auth_backup_hook", return_value=0) as mock_backup:
                    rc = proxy_manager.stop_proxy()
                    self.assertEqual(rc, 0)
                    mock_kill.assert_not_called()
                    self.assertFalse(self.state_file.exists())
                    self.assertFalse(self.backend_config_file.exists())
                    mock_backup.assert_called_with("backup")

    # --------------------------------------------------------------------------
    # 6. State-owned PIDs do not depend on optional Windows CIM metadata
    # --------------------------------------------------------------------------
    def test_v2_identity_metadata_mismatch_does_not_override_valid_state(self):
        state_data = {
            "state_version": 2,
            "pid": 55551,
            "sanitizer_pid": 55551,
            "backend_pid": 55552,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_expected",
            "backend_start_id": "sid_expected",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)

        with patch("proxy_manager.get_process_identity", return_value=("ok", "C:\\foreign\\python.exe", "different_start_id")):
            with patch("proxy_manager.get_process_command_line", return_value=None) as mock_cmdline:
                with patch("proxy_manager.check_proxy_health", return_value=(True, ["m1"])):
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "healthy")
                    mock_cmdline.assert_not_called()

    def test_v2_permission_denied_identity_is_still_managed(self):
        state_data = {
            "state_version": 2,
            "pid": 66661,
            "sanitizer_pid": 66661,
            "backend_pid": 66662,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_6",
            "backend_start_id": "sid_6",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)

        with patch("proxy_manager.get_process_identity", return_value=("permission_denied", None, None)):
            with patch("proxy_manager.check_proxy_health", return_value=(True, ["m1"])):
                status, _ = proxy_manager.verify_managed_state()
                self.assertEqual(status, "healthy")

    def test_v2_stop_uses_state_pids_when_command_line_unavailable(self):
        s_pid = 77771
        b_pid = 77772
        state_data = {
            "state_version": 2,
            "pid": s_pid,
            "sanitizer_pid": s_pid,
            "backend_pid": b_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_7",
            "backend_start_id": "sid_7",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        alive = {s_pid: True, b_pid: True}

        def mock_identity(pid):
            if not alive.get(pid, False):
                return ("not_found", None, None)
            if pid == s_pid:
                return ("ok", sys.executable, "sid_7")
            return ("ok", str(proxy_manager.get_proxy_exe_path().resolve()), "sid_7")

        def mock_terminate(pid):
            alive[pid] = False
            return True

        with patch("proxy_manager.get_process_identity", side_effect=mock_identity):
            with patch("proxy_manager.get_process_command_line", return_value=None) as mock_cmdline:
                with patch("proxy_manager.check_proxy_health", return_value=(True, ["m1"])):
                    with patch("proxy_manager.terminate_process", side_effect=mock_terminate) as mock_kill:
                        with patch("proxy_manager.run_auth_backup_hook", return_value=0):
                            rc = proxy_manager.stop_proxy()
                    self.assertEqual(mock_kill.call_count, 2)
                    self.assertEqual(rc, 0)
                    mock_cmdline.assert_not_called()

    # --------------------------------------------------------------------------
    # 7. Stop Tier Failure: State & Config Preserved
    # --------------------------------------------------------------------------
    def test_v2_stop_one_tier_fails_retains_state_and_backend_config(self):
        s_pid = 88881
        b_pid = 88882
        state_data = {
            "state_version": 2,
            "pid": s_pid,
            "sanitizer_pid": s_pid,
            "backend_pid": b_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_8",
            "backend_start_id": "sid_8",
            "port": 8090,
            "backend_port": 8095,
        }
        proxy_manager.write_state(state_data)
        self.backend_config_file.write_text("port: 8095\n", encoding="utf-8")

        # Backend fails to stop (still alive)
        alive_procs = {s_pid: True, b_pid: True}

        def mock_terminate(pid):
            if pid == s_pid:
                alive_procs[s_pid] = False
                return True
            # b_pid fails to terminate
            return False

        with patch("proxy_manager.verify_managed_state", return_value=("healthy", state_data)):
            with patch("proxy_manager.is_process_alive", side_effect=lambda p: alive_procs.get(p, False)):
                with patch("proxy_manager.terminate_process", side_effect=mock_terminate):
                    rc = proxy_manager.stop_proxy()
                    self.assertEqual(rc, 1, "Must return 1 when a process fails to terminate")
                    self.assertTrue(self.state_file.exists(), "State file must be retained when survivor exists")
                    self.assertTrue(self.backend_config_file.exists(), "Backend config must be retained when backend is still alive")

    # --------------------------------------------------------------------------
    # 8. Start Cleans Partial Survivor Before Starting New Pipeline
    # --------------------------------------------------------------------------
    def test_v2_start_cleans_partial_survivor_before_starting(self):
        state_data = {
            "state_version": 2,
            "pid": 99991,
            "sanitizer_pid": 99991,
            "backend_pid": 99992,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "backend_config": str(self.backend_config_file.resolve()),
            "process_start_id": "sid_9",
            "backend_start_id": "sid_9",
            "port": 8090,
            "backend_port": 8095,
        }

        call_sequence = []

        def mock_verify():
            if not call_sequence:
                call_sequence.append("verify_partial")
                return ("partial", state_data)
            call_sequence.append("verify_clean")
            return ("no_state", None)

        def mock_stop():
            call_sequence.append("stop_survivor")
            return 0

        with patch("proxy_manager.verify_managed_state", side_effect=mock_verify):
            with patch("proxy_manager.stop_proxy", side_effect=mock_stop):
                with patch("proxy_manager.is_port_in_use", return_value=True):
                    # Will abort on port conflict after stopping survivor
                    rc = proxy_manager.start_proxy()
                    self.assertEqual(rc, 1)
                    self.assertIn("stop_survivor", call_sequence, "start_proxy must call stop_proxy when state is partial")

    # --------------------------------------------------------------------------
    # 9. Port Conflict Does Not Kill Foreign Process
    # --------------------------------------------------------------------------
    def test_port_conflict_does_not_kill_foreign_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        bound_port = s.getsockname()[1]
        s.listen(1)

        os.environ["AIC_PORT"] = str(bound_port)
        try:
            with patch("proxy_manager.adopt_running_proxy", return_value=None):
                rc = proxy_manager.start_proxy()
                self.assertEqual(rc, 1, "start_proxy must return 1 when port is in use by foreign process")

                # Verify foreign socket is still alive
                test_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_conn.connect(("127.0.0.1", bound_port))
                test_conn.close()

                stop_rc = proxy_manager.stop_proxy()
                self.assertEqual(stop_rc, 0, "stop_proxy must return 0 without killing foreign process")
        finally:
            s.close()

    # --------------------------------------------------------------------------
    # 10. Windows-Only Integration Test: Windowless & Survives Parent Exit
    # --------------------------------------------------------------------------
    @unittest.skipUnless(sys.platform == "win32", "Windows-only test")
    def test_windows_spawn_daemon_hidden_survives_parent(self):
        """Verify spawn_daemon runs completely windowless (MainWindowHandle == 0)
        and survives parent launcher process exit on Windows."""
        launcher_code = """
import sys, os, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import proxy_manager

daemon_cmd = [sys.executable, "-c", "import time; time.sleep(15)"]
pid = proxy_manager.spawn_daemon(daemon_cmd, Path(sys.argv[2]))
print(pid)
sys.stdout.flush()
"""
        launcher_file = Path(self.temp_dir) / "launcher.py"
        launcher_file.write_text(launcher_code, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(launcher_file), str(ROOT_DIR / "scripts"), str(ROOT_DIR)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, f"Launcher failed: {proc.stderr}")
        daemon_pid_str = proc.stdout.strip()
        self.assertTrue(daemon_pid_str.isdigit(), f"Invalid PID output: {daemon_pid_str}")
        daemon_pid = int(daemon_pid_str)

        try:
            # 1. Daemon process exists and is alive after parent launcher exited
            self.assertTrue(proxy_manager.is_process_alive(daemon_pid), "Daemon process must survive parent exit")

            # 2. Confirm MainWindowHandle == 0 (no visible console or Windows Terminal window)
            ps_cmd = f"(Get-Process -Id {daemon_pid} -ErrorAction SilentlyContinue).MainWindowHandle"
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                handle = int(res.stdout.strip())
                self.assertEqual(handle, 0, f"Daemon must have MainWindowHandle == 0, got {handle}")
        finally:
            # Clean up dummy daemon process
            proxy_manager.terminate_process(daemon_pid)
            self.assertFalse(proxy_manager.is_process_alive(daemon_pid), "Dummy daemon must be stopped")


def test_proxy_manager():
    """Runner function for integration into run_tests.py."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestProxyManager)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        return True, f"All {result.testsRun} proxy manager test cases PASSED!"
    return False, f"Proxy manager tests failed: {len(result.failures)} failures, {len(result.errors)} errors."


if __name__ == "__main__":
    unittest.main()
