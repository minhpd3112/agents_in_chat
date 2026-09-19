#!/usr/bin/env python3
"""
Unit tests for safe 'aic update':
Tests operate strictly on isolated local temporary git fixtures (bare origin + working clone).

Scenarios covered:
1. Fetch failure / network error: abort gracefully, returns False.
2. Remote tracking missing / upstream removed: aborts cleanly.
3. Local working tree dirty: aborts before pull (staged, unstaged, untracked).
4. Detached HEAD: aborts cleanly.
5. Diverged branches: aborts cleanly without forced merge.
6. Merge conflicts simulated: aborts cleanly, working tree untouched.
7. Fast-forward pull success: updates cleanly.
8. Template cache sync when client version matches:
   a. exact match -> skip rewrite.
   b. client version matches but model list outdated -> updates cache.
   c. template has added/deleted models -> updates cache.
   d. template violates required invariants -> aborts cache reconcile without corrupting cache.
9. Cache file IO failure handling:
   a. temp file write failure -> cache unchanged.
   b. os.replace failure -> original cache restored/intact, unlocked state cleaned up.
   c. relock failure -> returns False and logs error.
10. Proxy running during update:
   a. verified managed proxy -> restarted cleanly after code update.
   b. foreign process on port -> does not kill, update succeeds but proxy not restarted.
   c. dead state file -> update proceeds without restart attempt.
   d. restart fails -> returns False and reports failure.
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

import check_updates
import proxy_manager


def remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def make_valid_model(slug="gemini-3.8-flash"):
    return {
        "slug": slug,
        "apply_patch_tool_type": "freeform",
        "tool_mode": "direct",
        "visibility": "list",
    }


class TestSafeUpdate(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="aic_git_test_"))
        self.bare_origin = self.temp_root / "origin.git"
        self.work_clone = self.temp_root / "work_clone"
        self.codex_dir = self.temp_root / ".codex"
        self.codex_dir.mkdir(parents=True, exist_ok=True)

        self.orig_env = os.environ.copy()
        os.environ["AIC_CODEX_DIR"] = str(self.codex_dir)
        os.environ["AIC_TEST_MODE"] = "1"

        # 1. Init bare origin and set default HEAD to refs/heads/main
        subprocess.run(["git", "init", "--bare", str(self.bare_origin)], capture_output=True, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(self.bare_origin), capture_output=True, check=True)

        # 2. Init seed repo and push to bare origin main
        self.seed_dir = self.temp_root / "seed"
        self.seed_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main", str(self.seed_dir)], capture_output=True, check=True)
        
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.seed_dir), check=True)

        # Create basic tree matching AIC structure with valid model invariants
        (self.seed_dir / "VERSION").write_text("1.0.0", encoding="utf-8")
        docs_dir = self.seed_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        self.default_template = {
            "client_version": "0.153.0",
            "models": [make_valid_model("gemini-3.7-flash")]
        }
        (docs_dir / "models_cache_template.json").write_text(json.dumps(self.default_template, indent=2), encoding="utf-8")

        scripts_dir = self.seed_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "proxy_manager.py").write_text("# dummy proxy manager\ndef read_state(): return None\ndef is_process_alive(pid): return False\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.bare_origin)], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(self.seed_dir), check=True)

        # 3. Clone working repo from bare origin
        subprocess.run(["git", "clone", str(self.bare_origin), str(self.work_clone)], capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(self.work_clone), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.work_clone), check=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.temp_root, onerror=remove_readonly)

    # 1. Fetch failure / network error
    def test_fetch_network_error_aborts(self):
        subprocess.run(["git", "remote", "set-url", "origin", str(self.temp_root / "nonexistent.git")], cwd=str(self.work_clone), check=True)
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when fetch fails")

    # 2. Remote tracking missing / upstream removed
    def test_remote_missing_aborts(self):
        subprocess.run(["git", "remote", "remove", "origin"], cwd=str(self.work_clone), check=True)
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when remote origin is missing")

    # 3. Local working tree dirty: staged, unstaged, untracked
    def test_dirty_worktree_untracked_aborts(self):
        (self.work_clone / "dirty.txt").write_text("dirty content", encoding="utf-8")
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when untracked file is present")
        self.assertTrue((self.work_clone / "dirty.txt").exists())

    def test_dirty_worktree_unstaged_aborts(self):
        (self.work_clone / "VERSION").write_text("1.0.0-modified", encoding="utf-8")
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when unstaged changes exist")

    def test_dirty_worktree_staged_aborts(self):
        (self.work_clone / "VERSION").write_text("1.0.0-staged", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.work_clone), check=True)
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when staged changes exist")

    # 4. Detached HEAD
    def test_detached_head_aborts(self):
        subprocess.run(["git", "checkout", "--detach", "HEAD"], cwd=str(self.work_clone), capture_output=True, check=True)
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort on detached HEAD")

    def test_wrong_branch_aborts(self):
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(self.work_clone), check=True)
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort when branch is not main")

    # 5. Diverged branches
    def test_divergent_branch_aborts_without_merge(self):
        # Push commit to origin
        (self.seed_dir / "origin_change.txt").write_text("remote change", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Remote change"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        # Local divergent commit
        (self.work_clone / "local_change.txt").write_text("local change", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(self.work_clone), check=True)
        subprocess.run(["git", "commit", "-m", "Local change"], cwd=str(self.work_clone), check=True)

        orig_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.work_clone), capture_output=True, text=True).stdout.strip()
        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok, "Update must abort on divergent branch")
        after_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.work_clone), capture_output=True, text=True).stdout.strip()
        self.assertEqual(orig_head, after_head)

    # 6. Simulated merge conflicts
    def test_simulated_merge_conflict_aborts_cleanly(self):
        # Conflict on VERSION file
        (self.seed_dir / "VERSION").write_text("2.0.0-remote", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Remote bump"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        (self.work_clone / "VERSION").write_text("2.0.0-local", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.work_clone), check=True)
        subprocess.run(["git", "commit", "-m", "Local bump"], cwd=str(self.work_clone), check=True)

        ok = check_updates.run_update(self.work_clone)
        self.assertFalse(ok)
        self.assertEqual((self.work_clone / "VERSION").read_text(encoding="utf-8").strip(), "2.0.0-local")

    # 7. Fast-forward success
    def test_fast_forward_success(self):
        (self.seed_dir / "VERSION").write_text("1.1.0", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Bump version"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        remote_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.seed_dir), capture_output=True, text=True).stdout.strip()
        ok = check_updates.run_update(self.work_clone)
        self.assertTrue(ok)
        local_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.work_clone), capture_output=True, text=True).stdout.strip()
        self.assertEqual(local_head, remote_sha)
        self.assertEqual((self.work_clone / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")

    # 8. Template cache sync tests
    def test_cache_sync_exact_match_skips_rewrite(self):
        cache_file = self.codex_dir / "models_cache.json"
        # First reconcile creates the file
        ok1 = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
        self.assertTrue(ok1)
        self.assertTrue(cache_file.exists())
        mtime_before = cache_file.stat().st_mtime_ns

        # Second reconcile should recognize exact semantic match and skip rewrite
        ok2 = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
        self.assertTrue(ok2)
        mtime_after = cache_file.stat().st_mtime_ns
        self.assertEqual(mtime_before, mtime_after, "Exact match must skip rewriting cache")

    def test_cache_sync_client_version_matches_outdated_models(self):
        cache_file = self.codex_dir / "models_cache.json"
        initial_cache = {
            "client_version": "0.153.0",
            "models": [make_valid_model("gemini-3.7-flash")]
        }
        cache_file.write_text(json.dumps(initial_cache, indent=2), encoding="utf-8")
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_file)], capture_output=True)
        else:
            os.chmod(cache_file, 0o444)

        # Update template in clone to add gpt-5.6-sol
        tpl_path = self.work_clone / "docs" / "models_cache_template.json"
        new_template = {
            "client_version": "0.153.0",
            "models": [
                make_valid_model("gemini-3.7-flash"),
                make_valid_model("gpt-5.6-sol")
            ]
        }
        tpl_path.write_text(json.dumps(new_template, indent=2), encoding="utf-8")

        ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
        self.assertTrue(ok)
        updated_data = json.loads(cache_file.read_text(encoding="utf-8"))
        slugs = [m["slug"] for m in updated_data["models"]]
        self.assertIn("gemini-3.7-flash", slugs)
        self.assertIn("gpt-5.6-sol", slugs)

    def test_cache_sync_added_and_deleted_models(self):
        cache_file = self.codex_dir / "models_cache.json"
        initial_cache = {
            "client_version": "0.153.0",
            "models": [make_valid_model("old-model")]
        }
        cache_file.write_text(json.dumps(initial_cache, indent=2), encoding="utf-8")
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_file)], capture_output=True)
        else:
            os.chmod(cache_file, 0o444)

        # Update template to have only new-model
        tpl_path = self.work_clone / "docs" / "models_cache_template.json"
        new_template = {
            "client_version": "0.153.0",
            "models": [make_valid_model("new-model")]
        }
        tpl_path.write_text(json.dumps(new_template, indent=2), encoding="utf-8")

        ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
        self.assertTrue(ok)
        updated_data = json.loads(cache_file.read_text(encoding="utf-8"))
        slugs = [m["slug"] for m in updated_data["models"]]
        self.assertEqual(slugs, ["new-model"])

    def test_cache_sync_template_violates_invariants_aborts(self):
        cache_file = self.codex_dir / "models_cache.json"
        initial_cache = {
            "client_version": "0.153.0",
            "models": [make_valid_model("gemini-3.7-flash")]
        }
        cache_file.write_text(json.dumps(initial_cache, indent=2), encoding="utf-8")
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_file)], capture_output=True)
        else:
            os.chmod(cache_file, 0o444)

        # Template missing visibility
        bad_tpl = {
            "client_version": "0.153.0",
            "models": [{
                "slug": "broken-model",
                "apply_patch_tool_type": "freeform",
                "tool_mode": "direct"
                # missing visibility: "list"
            }]
        }
        tpl_path = self.work_clone / "docs" / "models_cache_template.json"
        tpl_path.write_text(json.dumps(bad_tpl, indent=2), encoding="utf-8")

        ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
        self.assertFalse(ok, "Reconciliation must fail when template invariants are violated")
        # Cache must remain intact
        raw = cache_file.read_text(encoding="utf-8")
        self.assertIn("gemini-3.7-flash", raw)

    # 9. Cache file IO failure handling
    def test_cache_temp_file_write_failure(self):
        cache_file = self.codex_dir / "models_cache.json"
        cache_file.write_text(json.dumps({"client_version": "old"}, indent=2), encoding="utf-8")
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_file)], capture_output=True)
        else:
            os.chmod(cache_file, 0o444)

        orig_open = open
        def mock_open(file, *args, **kwargs):
            if ".tmp." in str(file):
                raise OSError("Disk full simulated")
            return orig_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
            self.assertFalse(ok)
            self.assertEqual(json.loads(cache_file.read_text(encoding="utf-8")), {"client_version": "old"})

    def test_cache_replace_failure(self):
        cache_file = self.codex_dir / "models_cache.json"
        cache_file.write_text(json.dumps({"client_version": "old"}, indent=2), encoding="utf-8")
        if sys.platform == "win32":
            subprocess.run(["attrib", "+r", str(cache_file)], capture_output=True)
        else:
            os.chmod(cache_file, 0o444)

        def mock_replace(src, dst):
            raise PermissionError("Simulated locked file")

        with patch("os.replace", side_effect=mock_replace):
            ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
            self.assertFalse(ok)
            # Ensure cache is still locked read-only and has old content
            self.assertTrue(check_updates.is_file_readonly(cache_file))
            self.assertEqual(json.loads(cache_file.read_text(encoding="utf-8")), {"client_version": "old"})

    def test_cache_relock_failure(self):
        with patch("check_updates.is_file_readonly", return_value=False):
            ok = check_updates.reconcile_models_cache(self.work_clone, self.codex_dir)
            self.assertFalse(ok, "Must return False when relock verification fails")

    # 10. Proxy running during update
    def test_update_restarts_healthy_proxy(self):
        (self.seed_dir / "VERSION").write_text("1.2.0", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Bump to 1.2.0"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        with patch.object(proxy_manager, "verify_managed_state", return_value=("healthy", 12345)):
            with patch.object(proxy_manager, "restart_proxy", return_value=0) as mock_restart:
                ok = check_updates.run_update(self.work_clone)
                self.assertTrue(ok)
                mock_restart.assert_called_once()

    def test_update_does_not_restart_untrusted_foreign_proxy(self):
        (self.seed_dir / "VERSION").write_text("1.2.1", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Bump to 1.2.1"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        with patch.object(proxy_manager, "verify_managed_state", return_value=("untrusted", 54321)):
            with patch.object(proxy_manager, "restart_proxy") as mock_restart:
                ok = check_updates.run_update(self.work_clone)
                self.assertTrue(ok, "Update should succeed even if foreign process is on port")
                mock_restart.assert_not_called()

    def test_update_does_not_restart_dead_proxy(self):
        (self.seed_dir / "VERSION").write_text("1.2.2", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Bump to 1.2.2"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        with patch.object(proxy_manager, "verify_managed_state", return_value=("dead", None)):
            with patch.object(proxy_manager, "restart_proxy") as mock_restart:
                ok = check_updates.run_update(self.work_clone)
                self.assertTrue(ok)
                mock_restart.assert_not_called()

    def test_update_fails_if_proxy_restart_fails(self):
        (self.seed_dir / "VERSION").write_text("1.2.3", encoding="utf-8")
        subprocess.run(["git", "add", "VERSION"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "commit", "-m", "Bump to 1.2.3"], cwd=str(self.seed_dir), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(self.seed_dir), check=True)

        with patch.object(proxy_manager, "verify_managed_state", return_value=("healthy", 12345)):
            with patch.object(proxy_manager, "restart_proxy", return_value=1) as mock_restart:
                ok = check_updates.run_update(self.work_clone)
                self.assertFalse(ok, "Update must return False if proxy restart fails")
                mock_restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()
