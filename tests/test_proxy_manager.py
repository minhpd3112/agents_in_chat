#!/usr/bin/env python3
"""
Comprehensive unit tests for proxy_manager.py:
1. Foreign process / port occupied does not get killed; start fails safely.
2. PID reuse (process alive but start_id or exe mismatch) does not kill foreign process; cleans stale state.
3. Dead process (stale PID) cleans state safely.
4. PermissionError (access denied) preserves state file, does not kill, and does NOT run backup hook.
5. Atomic state file write and strict schema validation (rejects bool, wrong types, malformed json).
6. Adoption: refuses when wrong config path, missing path after -config, or basename-only match.
7. Adoption: requires successful health check before online.
8. Adoption: write_state failure returns error without killing adopted process.
9. Start: failure to get identity or write state cleans spawned child safely.
10. Existing managed PID alive but unhealthy returns error and does NOT re-adopt.
11. Stop: timeout or access denied does NOT run backup hook.
12. Shared verification verify_managed_state does not consider foreign live PID managed.
"""

import os
import sys
import json
import socket
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import proxy_manager


class TestProxyManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aic_proxy_test_")
        self.state_file = Path(self.temp_dir) / ".proxy_state.json"
        self.auths_dir = Path(self.temp_dir) / "auths"
        self.auths_backup_dir = Path(self.temp_dir) / "auths_backup"
        self.auths_dir.mkdir(parents=True, exist_ok=True)
        self.auths_backup_dir.mkdir(parents=True, exist_ok=True)
        
        self.orig_env = os.environ.copy()
        os.environ["AIC_PROXY_STATE_FILE"] = str(self.state_file)
        os.environ["AIC_AUTHS_DIR"] = str(self.auths_dir)
        os.environ["AIC_AUTHS_BACKUP_DIR"] = str(self.auths_backup_dir)
        os.environ["AIC_TEST_MODE"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_state_file_atomic_write_and_strict_schema(self):
        data = {
            "state_version": 1,
            "pid": 12345,
            "exe": "C:\\test\\cli-proxy-api.exe",
            "config": "C:\\test\\config.yaml",
            "process_start_id": "134333332327778296",
            "port": 8080
        }
        ok = proxy_manager.write_state(data)
        self.assertTrue(ok)
        self.assertTrue(self.state_file.exists())
        
        # Verify no BOM
        raw_bytes = self.state_file.read_bytes()
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"), "State file must not have UTF-8 BOM")
        
        read_data = proxy_manager.read_state()
        self.assertEqual(read_data, data)
        
        # Test schema rejection: bool pid
        invalid_pid = data.copy()
        invalid_pid["pid"] = True
        self.assertFalse(proxy_manager.validate_state(invalid_pid))
        
        # Test schema rejection: non-int port
        invalid_port = data.copy()
        invalid_port["port"] = "8080"
        self.assertFalse(proxy_manager.validate_state(invalid_port))

        # Test schema rejection: empty process_start_id
        invalid_sid = data.copy()
        invalid_sid["process_start_id"] = "   "
        self.assertFalse(proxy_manager.validate_state(invalid_sid))

        # Malformed state in file should not crash read_state
        self.state_file.write_text("{ corrupt json", encoding="utf-8")
        self.assertIsNone(proxy_manager.read_state())

        proxy_manager.remove_state()
        self.assertFalse(self.state_file.exists())

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
                
                # Verify foreign socket is still alive and listening
                test_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_conn.connect(("127.0.0.1", bound_port))
                test_conn.close()
                
                stop_rc = proxy_manager.stop_proxy()
                self.assertEqual(stop_rc, 0, "stop_proxy must return 0 without killing foreign process")
        finally:
            s.close()

    def test_pid_reuse_mismatch_start_id_not_killed(self):
        current_pid = os.getpid()
        state_data = {
            "state_version": 1,
            "pid": current_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(ROOT_DIR / "config.yaml"),
            "process_start_id": "999999999999999999",  # Definitely mismatch
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        with patch("proxy_manager.terminate_process") as mock_kill:
            with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                rc = proxy_manager.stop_proxy()
                self.assertEqual(rc, 0)
                mock_kill.assert_not_called()
                self.assertFalse(self.state_file.exists())
                # Should NOT run backup hook when state was untrusted
                mock_backup.assert_not_called()

    def test_crafted_state_foreign_executable_not_killed(self):
        current_pid = os.getpid()
        status, exe_path, start_id = proxy_manager.get_process_identity(current_pid)
        state_data = {
            "state_version": 1,
            "pid": current_pid,
            "exe": "C:\\completely\\different\\fake.exe",
            "config": str(ROOT_DIR / "config.yaml"),
            "process_start_id": start_id or "12345",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        with patch("proxy_manager.terminate_process") as mock_kill:
            with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                rc = proxy_manager.stop_proxy()
                self.assertEqual(rc, 0)
                mock_kill.assert_not_called()
                self.assertFalse(self.state_file.exists())
                mock_backup.assert_not_called()

    def _setup_valid_state(self):
        current_pid = os.getpid()
        status, exe_path, start_id = proxy_manager.get_process_identity(current_pid)
        state_data = {
            "state_version": 1,
            "pid": current_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "process_start_id": start_id or "12345",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        return current_pid, start_id or "12345"

    def test_fail_closed_contract_a_none_cmdline(self):
        pid, start_id = self._setup_valid_state()
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value=None):
                with patch("proxy_manager.check_proxy_health") as mock_health:
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "untrusted")
                    mock_health.assert_not_called()

                with patch("proxy_manager.terminate_process") as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_not_called()
                        mock_backup.assert_not_called()
                        self.assertFalse(self.state_file.exists())

    def test_fail_closed_contract_b_empty_cmdline(self):
        pid, start_id = self._setup_valid_state()
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value="   "):
                with patch("proxy_manager.check_proxy_health") as mock_health:
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "untrusted")
                    mock_health.assert_not_called()

                with patch("proxy_manager.terminate_process") as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_not_called()
                        mock_backup.assert_not_called()

    def test_fail_closed_contract_c_missing_config_flag(self):
        pid, start_id = self._setup_valid_state()
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value="cli-proxy-api --port 8080"):
                with patch("proxy_manager.check_proxy_health") as mock_health:
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "untrusted")
                    mock_health.assert_not_called()

                with patch("proxy_manager.terminate_process") as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_not_called()
                        mock_backup.assert_not_called()

    def test_fail_closed_contract_d_config_flag_without_value(self):
        pid, start_id = self._setup_valid_state()
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value="cli-proxy-api -config"):
                with patch("proxy_manager.check_proxy_health") as mock_health:
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "untrusted")
                    mock_health.assert_not_called()

                with patch("proxy_manager.terminate_process") as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_not_called()
                        mock_backup.assert_not_called()

    def test_fail_closed_contract_e_different_dir_same_basename(self):
        pid, start_id = self._setup_valid_state()
        other_config = Path(self.temp_dir) / "other_dir" / "config.yaml"
        cmdline = f'cli-proxy-api -config "{other_config}"'
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value=cmdline):
                with patch("proxy_manager.check_proxy_health") as mock_health:
                    status, _ = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "untrusted")
                    mock_health.assert_not_called()

                with patch("proxy_manager.terminate_process") as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_not_called()
                        mock_backup.assert_not_called()

    def test_fail_closed_contract_f_valid_config(self):
        pid, start_id = self._setup_valid_state()
        canonical_cfg = str(proxy_manager.get_config_path().resolve())
        cmdline = f'cli-proxy-api -config "{canonical_cfg}"'
        with patch("proxy_manager.get_process_identity", return_value=("ok", str(proxy_manager.get_proxy_exe_path().resolve()), start_id)):
            with patch("proxy_manager.get_process_command_line", return_value=cmdline):
                with patch("proxy_manager.check_proxy_health", return_value=(True, ["model1"])) as mock_health:
                    status, state = proxy_manager.verify_managed_state()
                    self.assertEqual(status, "healthy")
                    mock_health.assert_called_once()

                with patch("proxy_manager.terminate_process", return_value=True) as mock_kill:
                    with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                        rc = proxy_manager.stop_proxy()
                        self.assertEqual(rc, 0)
                        mock_kill.assert_called_with(pid)
                        mock_backup.assert_called_with("backup")
                        self.assertFalse(self.state_file.exists())

    def test_stale_pid_dead_process_cleans_state_and_runs_backup(self):
        state_data = {
            "state_version": 1,
            "pid": 999999,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(ROOT_DIR / "config.yaml"),
            "process_start_id": "12345",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        with patch("proxy_manager.get_process_identity", return_value=("not_found", None, None)):
            with patch("proxy_manager.terminate_process") as mock_kill:
                with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                    rc = proxy_manager.stop_proxy()
                    self.assertEqual(rc, 0)
                    mock_kill.assert_not_called()
                    self.assertFalse(self.state_file.exists())
                    mock_backup.assert_called_with("backup")

    def test_permission_denied_preserves_state_and_does_not_kill_or_backup(self):
        state_data = {
            "state_version": 1,
            "pid": 4,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(ROOT_DIR / "config.yaml"),
            "process_start_id": "123",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        with patch("proxy_manager.get_process_identity", return_value=("permission_denied", None, None)):
            with patch("proxy_manager.terminate_process") as mock_kill:
                with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                    rc = proxy_manager.stop_proxy()
                    self.assertEqual(rc, 1, "Must return error when process is permission denied")
                    mock_kill.assert_not_called()
                    mock_backup.assert_not_called()
                    self.assertTrue(self.state_file.exists())

    def test_stop_timeout_does_not_run_backup(self):
        current_pid = os.getpid()
        status, exe_path, start_id = proxy_manager.get_process_identity(current_pid)
        state_data = {
            "state_version": 1,
            "pid": current_pid,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "process_start_id": start_id or "123",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        with patch("proxy_manager.verify_managed_state", return_value=("healthy", state_data)):
            with patch("proxy_manager.terminate_process", return_value=False):  # Timeout
                with patch("proxy_manager.run_auth_backup_hook") as mock_backup:
                    rc = proxy_manager.stop_proxy()
                    self.assertEqual(rc, 1)
                    # Must NOT backup if terminate timed out
                    mock_backup.assert_not_called()
                    self.assertTrue(self.state_file.exists())

    def test_adopt_refuses_when_wrong_config_path_or_missing(self):
        # 1. Config arg is missing path: -config
        self.assertIsNone(proxy_manager.parse_config_from_cmdline("cli-proxy-api -config"))
        
        # 2. Config arg is missing completely
        self.assertIsNone(proxy_manager.parse_config_from_cmdline("cli-proxy-api --verbose"))
        
        # 3. Config points to different path with same basename
        cmdline = 'cli-proxy-api -config "C:\\other_dir\\config.yaml"'
        parsed = proxy_manager.parse_config_from_cmdline(cmdline)
        self.assertEqual(parsed, "C:\\other_dir\\config.yaml")
        # Canonical comparison must fail against repo config
        self.assertFalse(proxy_manager.is_same_canonical_path(Path(parsed), proxy_manager.get_config_path()))

    def test_adopt_unhealthy_does_not_report_online(self):
        with patch("proxy_manager.get_proxy_exe_path") as mock_exe:
            fake_exe = Path(self.temp_dir) / "cli-proxy-api.exe"
            fake_exe.touch()
            mock_exe.return_value = fake_exe
            
            with patch("proxy_manager.check_proxy_health", return_value=(False, [])):
                # Health check failed -> adopt must return None
                self.assertIsNone(proxy_manager.adopt_running_proxy())

    def test_adopt_write_state_failure_does_not_kill_adopted(self):
        with patch("proxy_manager.adopt_running_proxy", return_value=(55555, "start_id_555")):
            with patch("proxy_manager.write_state", return_value=False):
                with patch("proxy_manager.terminate_process") as mock_kill:
                    rc = proxy_manager.start_proxy()
                    self.assertEqual(rc, 1)
                    mock_kill.assert_not_called()

    def test_start_fails_and_cleans_child_when_identity_fails(self):
        mock_proc = MagicMock()
        mock_proc.pid = 44444
        with patch("proxy_manager.verify_managed_state", return_value=("no_state", None)):
            with patch("proxy_manager.adopt_running_proxy", return_value=None):
                with patch("proxy_manager.is_port_in_use", return_value=False):
                    with patch("proxy_manager.run_auth_backup_hook"):
                        with patch("subprocess.Popen", return_value=mock_proc):
                            # Identity fails
                            with patch("proxy_manager.get_process_identity", return_value=("error", None, None)):
                                with patch("proxy_manager.terminate_process") as mock_kill:
                                    rc = proxy_manager.start_proxy()
                                    self.assertEqual(rc, 1)
                                    mock_kill.assert_called_with(44444)

    def test_start_fails_and_cleans_child_when_write_state_fails(self):
        mock_proc = MagicMock()
        mock_proc.pid = 33333
        with patch("proxy_manager.verify_managed_state", return_value=("no_state", None)):
            with patch("proxy_manager.adopt_running_proxy", return_value=None):
                with patch("proxy_manager.is_port_in_use", return_value=False):
                    with patch("proxy_manager.run_auth_backup_hook"):
                        with patch("subprocess.Popen", return_value=mock_proc):
                            with patch("proxy_manager.get_process_identity", return_value=("ok", "/path/exe", "sid123")):
                                with patch("proxy_manager.write_state", return_value=False):
                                    with patch("proxy_manager.terminate_process") as mock_kill:
                                        rc = proxy_manager.start_proxy()
                                        self.assertEqual(rc, 1)
                                        mock_kill.assert_called_with(33333)

    def test_managed_pid_alive_but_unhealthy_not_readopted(self):
        state_data = {
            "state_version": 1,
            "pid": 22222,
            "exe": str(proxy_manager.get_proxy_exe_path().resolve()),
            "config": str(proxy_manager.get_config_path().resolve()),
            "process_start_id": "sid222",
            "port": 8080
        }
        with patch("proxy_manager.verify_managed_state", return_value=("unhealthy", state_data)):
            with patch("proxy_manager.adopt_running_proxy") as mock_adopt:
                rc = proxy_manager.start_proxy()
                self.assertEqual(rc, 1)
                mock_adopt.assert_not_called()

    def test_updater_does_not_consider_foreign_live_pid_managed(self):
        current_pid = os.getpid()
        status, exe_path, start_id = proxy_manager.get_process_identity(current_pid)
        state_data = {
            "state_version": 1,
            "pid": current_pid,
            "exe": "C:\\foreign\\python.exe",
            "config": str(ROOT_DIR / "config.yaml"),
            "process_start_id": start_id or "123",
            "port": 8080
        }
        proxy_manager.write_state(state_data)
        
        status, _ = proxy_manager.verify_managed_state()
        self.assertEqual(status, "untrusted", "Foreign live PID must be verified as untrusted")


if __name__ == "__main__":
    unittest.main()
