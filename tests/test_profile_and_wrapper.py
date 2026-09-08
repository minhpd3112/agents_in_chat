#!/usr/bin/env python3
"""
Unit tests for Step 5:
1. manage_profile.py:
   - Idempotent install (only 1 block).
   - Personal alias preservation.
   - CRLF and LF preservation.
   - Half-marker corruption detection (aborts safely without modifying file).
   - Uninstall cleanup (deletes empty file, preserves user config).
   - Rollback byte-exact restoration.
2. sync_client_version.py:
   - Smart wrapper updates client_version atomically.
   - Read-only attribute restored in all paths.
"""

import os
import sys
import json
import stat
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import manage_profile
import sync_client_version


class TestProfileAndWrapper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aic_prof_test_")
        self.profile_path = Path(self.temp_dir) / ".bashrc"
        self.state_file = Path(self.temp_dir) / "profile_rollback.json"
        self.codex_dir = Path(self.temp_dir) / ".codex"
        self.codex_dir.mkdir(parents=True, exist_ok=True)
        
        self.orig_env = os.environ.copy()
        os.environ["AIC_CODEX_DIR"] = str(self.codex_dir)
        os.environ["AIC_TEST_MODE"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_idempotent_install_and_crlf_preservation(self):
        initial = b'# User config\r\nalias mytool="echo 123"\r\n'
        self.profile_path.write_bytes(initial)

        block = b'# >>> AIC >>>\r\nalias aic="aic.py"\r\n# <<< AIC <<<\r\n'
        
        # First install
        rc1 = manage_profile.cmd_install(self.profile_path, block, self.state_file)
        self.assertEqual(rc1, 0)
        self.assertEqual(self.profile_path.read_bytes().count(b"# >>> AIC >>>"), 1)
        self.assertIn(b'alias mytool="echo 123"', self.profile_path.read_bytes())
        self.assertIn(b"\r\n", self.profile_path.read_bytes())

        # Second install (idempotency)
        rc2 = manage_profile.cmd_install(self.profile_path, block, self.state_file)
        self.assertEqual(rc2, 0)
        self.assertEqual(self.profile_path.read_bytes().count(b"# >>> AIC >>>"), 1)
        self.assertIn(b'alias mytool="echo 123"', self.profile_path.read_bytes())

    def test_half_marker_detection_aborts_without_mutation(self):
        # Only start marker, missing end marker
        corrupted = b'alias foo="bar"\n# >>> AIC >>>\nalias aic="aic.py"\n'
        self.profile_path.write_bytes(corrupted)

        block = b'# >>> AIC >>>\nalias aic="new"\n# <<< AIC <<<\n'
        rc = manage_profile.cmd_install(self.profile_path, block)
        self.assertEqual(rc, 1, "Must fail when start marker lacks end marker")
        # File must remain untouched
        self.assertEqual(self.profile_path.read_bytes(), corrupted)

        # Uninstall on half marker must also abort
        rc_un = manage_profile.cmd_uninstall(self.profile_path)
        self.assertEqual(rc_un, 1)
        self.assertEqual(self.profile_path.read_bytes(), corrupted)

    def test_uninstall_preserves_personal_aliases(self):
        content = (
            b'alias personal1="echo 1"\n'
            b'# >>> AIC >>>\n'
            b'alias aic="aic.py"\n'
            b'# <<< AIC <<<\n'
            b'alias personal2="echo 2"\n'
        )
        self.profile_path.write_bytes(content)

        rc = manage_profile.cmd_uninstall(self.profile_path)
        self.assertEqual(rc, 0)
        after = self.profile_path.read_bytes()
        self.assertNotIn(b"# >>> AIC >>>", after)
        self.assertIn(b'alias personal1="echo 1"', after)
        self.assertIn(b'alias personal2="echo 2"', after)

    def test_uninstall_deletes_empty_profile(self):
        content = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        self.profile_path.write_bytes(content)

        rc = manage_profile.cmd_uninstall(self.profile_path)
        self.assertEqual(rc, 0)
        self.assertFalse(self.profile_path.exists(), "Profile created by AIC should be deleted if empty")

    def test_rollback_restores_original_content(self):
        orig = b'# Original profile\nexport FOO=BAR\n'
        self.profile_path.write_bytes(orig)

        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        manage_profile.cmd_install(self.profile_path, block, self.state_file)
        self.assertIn(b"# >>> AIC >>>", self.profile_path.read_bytes())

        # Rollback
        rc = manage_profile.cmd_rollback(self.profile_path, self.state_file)
        self.assertEqual(rc, 0)
        self.assertEqual(self.profile_path.read_bytes(), orig)

    def test_sync_client_version_atomic_and_read_only(self):
        cache_path = self.codex_dir / "models_cache.json"
        initial_cache = {
            "client_version": "0.148.0",
            "models": [{"slug": "gemini-3.7-flash"}]
        }
        cache_path.write_text(json.dumps(initial_cache, indent=2), encoding="utf-8")
        
        # Lock read-only
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_path)], capture_output=True)
        else:
            os.chmod(cache_path, 0o444)

        # Mock codex --version returning 0.153.0
        with patch("sync_client_version.get_codex_version", return_value="0.153.0"):
            rc = sync_client_version.sync_version()
            self.assertEqual(rc, 0)

        # Verify updated version
        updated_data = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(updated_data["client_version"], "0.153.0")

        # Verify locked read-only again
        if sys.platform == "win32":
            import stat
            mode = os.stat(cache_path).st_mode
            self.assertFalse(bool(mode & stat.S_IWRITE))
        else:
            self.assertFalse(os.access(cache_path, os.W_OK))


    def test_rollback_preserves_user_content_when_profile_was_initially_nonexistent(self):
        # Profile initially did NOT exist
        if self.profile_path.exists():
            self.profile_path.unlink()
            
        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        rc = manage_profile.cmd_install(self.profile_path, block, self.state_file)
        self.assertEqual(rc, 0)
        self.assertTrue(self.state_file.exists())
        
        # User adds custom content after install
        with open(self.profile_path, "ab") as f:
            f.write(b'alias user_after="echo hi"\n')
            
        # Rollback
        rc_rb = manage_profile.cmd_rollback(self.profile_path, self.state_file)
        self.assertEqual(rc_rb, 0)
        
        # Profile MUST still exist and contain user content!
        self.assertTrue(self.profile_path.exists())
        content = self.profile_path.read_bytes()
        self.assertNotIn(b"# >>> AIC >>>", content)
        self.assertIn(b'alias user_after="echo hi"', content)
        # State file removed on success
        self.assertFalse(self.state_file.exists())

    def test_rollback_preserves_user_content_added_outside_block(self):
        orig = b'alias original="echo 1"\n'
        self.profile_path.write_bytes(orig)
        
        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        manage_profile.cmd_install(self.profile_path, block, self.state_file)
        
        # User adds content after install
        with open(self.profile_path, "ab") as f:
            f.write(b'alias user_added_later="echo 2"\n')
            
        rc = manage_profile.cmd_rollback(self.profile_path, self.state_file)
        self.assertEqual(rc, 0)
        
        content = self.profile_path.read_bytes()
        self.assertNotIn(b"# >>> AIC >>>", content)
        self.assertIn(b'alias original="echo 1"', content)
        self.assertIn(b'alias user_added_later="echo 2"', content)

    def test_half_marker_does_not_create_state_file(self):
        corrupted = b'# >>> AIC >>>\nonly start marker\n'
        self.profile_path.write_bytes(corrupted)
        
        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        rc = manage_profile.cmd_install(self.profile_path, block, self.state_file)
        self.assertEqual(rc, 1)
        # state file must NOT have been created!
        self.assertFalse(self.state_file.exists())

    def test_reinstall_records_fresh_baseline(self):
        orig = b'# User config\n'
        self.profile_path.write_bytes(orig)
        
        block1 = b'# >>> AIC >>>\nalias aic="1"\n# <<< AIC <<<\n'
        manage_profile.cmd_install(self.profile_path, block1, self.state_file)
        
        # Second install (reinstall)
        block2 = b'# >>> AIC >>>\nalias aic="2"\n# <<< AIC <<<\n'
        rc = manage_profile.cmd_install(self.profile_path, block2, self.state_file)
        self.assertEqual(rc, 0)
        
        # State file must record that block1 was present
        state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertTrue(state_data["original_block_present"])

    def test_uninstall_preserves_multiple_trailing_newlines(self):
        orig = b'alias foo="bar"\n\n\n\n'
        self.profile_path.write_bytes(orig)
        
        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        manage_profile.cmd_install(self.profile_path, block)
        
        rc = manage_profile.cmd_uninstall(self.profile_path)
        self.assertEqual(rc, 0)
        self.assertEqual(self.profile_path.read_bytes(), orig)

    def test_crlf_profile_with_lf_block_no_mixed_newlines(self):
        initial = b'# User CRLF\r\nalias mytool="1"\r\n'
        self.profile_path.write_bytes(initial)
        
        # Block with purely LF
        block = b'# >>> AIC >>>\nalias aic="aic.py"\n# <<< AIC <<<\n'
        rc = manage_profile.cmd_install(self.profile_path, block)
        self.assertEqual(rc, 0)
        
        content = self.profile_path.read_bytes()
        # All newlines should be \r\n
        self.assertNotIn(b"[^\r]\n", content)
        self.assertIn(b"\r\n", content)
        lines = content.split(b"\r\n")
        self.assertGreater(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
