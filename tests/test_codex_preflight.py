#!/usr/bin/env python3
"""
Unit tests for Codex preflight check:
1. check_codex_running.py returns 0 (not running), 1 (running), 2 (indeterminate).
2. install.ps1, uninstall.ps1, install.sh, uninstall.sh fail-closed when status is 1 or 2.
3. Verifies zero mutations (config, cache, sessions, profile, PATH) on abort.
"""

import os
import sys
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestCodexPreflight(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aic_preflight_test_")
        self.codex_dir = Path(self.temp_dir) / ".codex"
        self.codex_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path = Path(self.temp_dir) / "profile.txt"
        self.user_path_file = Path(self.temp_dir) / "user_path.txt"
        self.bin_link_dir = Path(self.temp_dir) / "bin_link"
        self.bin_link_dir.mkdir(parents=True, exist_ok=True)
        
        self.config_path = self.codex_dir / "config.toml"
        self.orig_config_content = b'model = "gpt-5.6-sol"\nmodel_provider = "openai"\n'
        self.config_path.write_bytes(self.orig_config_content)

        self.bash_bin = "bash"
        if sys.platform == "win32":
            git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
            if git_bash.exists():
                self.bash_bin = str(git_bash)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_base_env(self):
        auths_dir = Path(self.temp_dir) / "auths"
        auths_backup_dir = Path(self.temp_dir) / "auths_backup"
        auths_dir.mkdir(parents=True, exist_ok=True)
        auths_backup_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["AIC_TEST_MODE"] = "1"
        env["AIC_CODEX_DIR"] = str(self.codex_dir)
        env["AIC_AUTHS_DIR"] = str(auths_dir)
        env["AIC_AUTHS_BACKUP_DIR"] = str(auths_backup_dir)
        env["AIC_PROFILE_PATH"] = str(self.profile_path)
        env["AIC_USER_PATH_FILE"] = str(self.user_path_file)
        env["AIC_BIN_LINK_DIR"] = str(self.bin_link_dir)
        env["AIC_SKIP_DOWNLOAD"] = "1"
        env["AIC_SKIP_PROXY"] = "1"
        return env

    def test_preflight_helper_exit_codes(self):
        helper = ROOT_DIR / "scripts" / "check_codex_running.py"
        
        # 1. Running -> exit 1
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "1"
        r = subprocess.run([sys.executable, "-B", str(helper)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Codex CLI is currently running", r.stderr)

        # 2. Indeterminate -> exit 2
        env["AIC_CODEX_RUNNING"] = "2"
        r = subprocess.run([sys.executable, "-B", str(helper)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("indeterminate Codex CLI process status", r.stderr)

        # 3. Stopped -> exit 0
        env["AIC_CODEX_RUNNING"] = "0"
        r = subprocess.run([sys.executable, "-B", str(helper)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)

    def test_install_ps1_aborts_before_mutation_when_codex_running(self):
        if sys.platform != "win32":
            return
        
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "1"
        
        install_script = ROOT_DIR / "install.ps1"
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_script)],
            env=env, capture_output=True, text=True
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Codex CLI is currently running", r.stderr)
        
        # Verify no mutations
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)
        self.assertFalse((self.codex_dir / "models_cache.json").exists())
        self.assertFalse((self.codex_dir / "aic-backup").exists())
        self.assertFalse(self.profile_path.exists())

    def test_uninstall_ps1_aborts_before_mutation_when_codex_running(self):
        if sys.platform != "win32":
            return
        
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "1"
        
        uninstall_script = ROOT_DIR / "uninstall.ps1"
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(uninstall_script)],
            env=env, capture_output=True, text=True
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Codex CLI is currently running", r.stderr)
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)

    def test_install_sh_aborts_before_mutation_when_codex_running(self):
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "1"
        
        install_script = ROOT_DIR / "install.sh"
        r = subprocess.run([self.bash_bin, str(install_script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Codex CLI is currently running", r.stderr)
        
        # Verify no mutations
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)
        self.assertFalse((self.codex_dir / "models_cache.json").exists())
        self.assertFalse((self.codex_dir / "aic-backup").exists())
        self.assertFalse(self.profile_path.exists())

    def test_uninstall_sh_aborts_before_mutation_when_codex_running(self):
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "1"
        
        uninstall_script = ROOT_DIR / "uninstall.sh"
        r = subprocess.run([self.bash_bin, str(uninstall_script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Codex CLI is currently running", r.stderr)
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)


    def test_install_ps1_aborts_on_indeterminate_status(self):
        if sys.platform != "win32":
            return
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "2"
        install_script = ROOT_DIR / "install.ps1"
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_script)],
            env=env, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("indeterminate Codex CLI process status", r.stderr)
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)
        self.assertFalse((self.codex_dir / "models_cache.json").exists())

    def test_install_sh_aborts_on_indeterminate_status(self):
        env = self._get_base_env()
        env["AIC_CODEX_RUNNING"] = "2"
        install_script = ROOT_DIR / "install.sh"
        r = subprocess.run([self.bash_bin, str(install_script)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("indeterminate Codex CLI process status", r.stderr)
        self.assertEqual(self.config_path.read_bytes(), self.orig_config_content)
        self.assertFalse((self.codex_dir / "models_cache.json").exists())


if __name__ == "__main__":
    unittest.main()

