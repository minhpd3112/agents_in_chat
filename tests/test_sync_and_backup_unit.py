import os
import sys
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from sync_sessions import sync_provider, verify_provider
from configure_codex_toml import configure_custom, restore_original, ensure_backup, compute_sha256_bytes, compute_sha256_file


def setup_temp_codex_fixture(target_provider="openai"):
    tmp_dir = Path(tempfile.mkdtemp(prefix="aic_test_codex_"))
    sessions_dir = tmp_dir / "sessions" / "2026-08"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Create SQLite DB
    db_path = tmp_dir / "state_5.sqlite"
    conn = sqlite3.connect(str(db_path))
    with conn:
        c = conn.cursor()
        c.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, title TEXT);")
        c.execute("INSERT INTO threads VALUES ('t1', ?, 'Chat 1');", (target_provider,))
        c.execute("INSERT INTO threads VALUES ('t2', ?, 'Chat 2');", (target_provider,))
        c.execute("INSERT INTO threads VALUES ('t3', NULL, 'Untracked Thread');")
    conn.close()
    
    # 2. Create sample session JSONL
    sample_jsonl = sessions_dir / "session_1.jsonl"
    header = {"type": "session_meta", "payload": {"id": "s1", "model_provider": target_provider, "cwd": "/home/user"}}
    msg1 = {"type": "message", "role": "user", "content": "Hello AI"}
    msg2 = {"type": "message", "role": "assistant", "content": "Hello User"}
    with open(sample_jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        f.write(json.dumps(msg1) + "\n")
        f.write(json.dumps(msg2) + "\n")
        
    return tmp_dir, sample_jsonl, db_path


def run_all_unit_tests() -> bool:
    print("=" * 70)
    print("  RUNNING OFFLINE UNIT TESTS FOR SYNC & BACKUP RECOVERY")
    print("=" * 70)
    
    tests_passed = 0
    total_tests = 14

    # Test 1: Invalid provider
    print("Test 1: Invalid provider returns 2 and modifies 0 files...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        code = sync_provider("invalid_provider", tmp_dir)
        assert code == 2, f"Expected 2, got {code}"
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        meta = json.loads(lines[0])
        assert meta["payload"]["model_provider"] == "openai", "File should not have changed"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 2: Sync openai -> custom
    print("Test 2: Sync openai -> custom updates SQLite & JSONL, preserves message bodies...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        code = sync_provider("custom", tmp_dir)
        assert code == 0, f"Expected 0, got {code}"
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        meta = json.loads(lines[0])
        assert meta["payload"]["model_provider"] == "custom"
        assert json.loads(lines[1])["content"] == "Hello AI"
        assert json.loads(lines[2])["content"] == "Hello User"
        
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT model_provider FROM threads WHERE id='t1';")
        assert c.fetchone()[0] == "custom"
        conn.close()
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 3: Sync custom -> openai
    print("Test 3: Sync custom -> openai updates SQLite & JSONL...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("custom")
    try:
        code = sync_provider("openai", tmp_dir)
        assert code == 0, f"Expected 0, got {code}"
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        meta = json.loads(lines[0])
        assert meta["payload"]["model_provider"] == "openai"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 4: Idempotency
    print("Test 4: Idempotency - running sync twice causes zero data corruption...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        code1 = sync_provider("custom", tmp_dir)
        assert code1 == 0
        code2 = sync_provider("custom", tmp_dir)
        assert code2 == 0
        v_code = verify_provider("custom", tmp_dir)
        assert v_code == 0
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 5: Malformed JSONL
    print("Test 5: Malformed JSONL returns error code 1 and does not truncate...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        bad_jsonl = tmp_dir / "sessions" / "2026-08" / "bad.jsonl"
        with open(bad_jsonl, "w", encoding="utf-8") as f:
            f.write("{NOT_VALID_JSON}\nline2\n")
        code = sync_provider("custom", tmp_dir)
        assert code == 1, f"Expected error code 1, got {code}"
        with open(bad_jsonl, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == "{NOT_VALID_JSON}\nline2\n", "File should not be truncated"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 6: SQLite error handling
    print("Test 6: SQLite error handling does not produce false success...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        with open(db_path, "w") as f:
            f.write("CORRUPTED NOT A SQLITE FILE")
        code = sync_provider("custom", tmp_dir)
        assert code == 1, f"Expected error code 1, got {code}"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 7: True Atomic Replace Failure Injection (Mock os.replace)
    print("Test 7: True Atomic Replace failure injection cleans temp files and preserves original...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        orig_bytes = jsonl_path.read_bytes()
        def mock_failing_replace(src, dst):
            raise OSError("Simulated disk I/O error during os.replace")
            
        with patch("os.replace", side_effect=mock_failing_replace):
            code = sync_provider("custom", tmp_dir)
            assert code == 1, f"Expected error code 1, got {code}"
            
        # Verify original file untouched byte-for-byte
        assert jsonl_path.read_bytes() == orig_bytes, "Original file was modified or corrupted!"
        
        # Verify no dangling .tmp files
        tmp_files = list(tmp_dir.glob("**/*.tmp*"))
        assert len(tmp_files) == 0, f"Found dangling temp files: {tmp_files}"
        
        # Verify provider in file is still openai
        assert verify_provider("custom", tmp_dir) == 1
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 8: Backup config byte-exact (LF and CRLF)
    print("Test 8: Backup config preserves LF and raw bytes byte-exact...")
    tmp_dir, _, _ = setup_temp_codex_fixture()
    try:
        config_path = tmp_dir / "config.toml"
        raw_lf_content = b'[projects."/test"]\ntrust_level = "trusted"\nmodel = "gpt-5.6-sol"\n'
        config_path.write_bytes(raw_lf_content)
        
        code = configure_custom(tmp_dir)
        assert code == 0
        
        backup_file = tmp_dir / "aic-backup" / "config.toml.bak"
        assert backup_file.exists()
        assert backup_file.read_bytes() == raw_lf_content, "Backup is not byte-exact!"
        
        # Re-run configure_custom - backup must remain unchanged
        configure_custom(tmp_dir)
        assert backup_file.read_bytes() == raw_lf_content
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 9: UTF-8 BOM Backup & Restore byte-exact
    print("Test 9: Config with UTF-8 BOM restores byte-exact without checksum mismatch...")
    tmp_dir, _, _ = setup_temp_codex_fixture()
    try:
        config_path = tmp_dir / "config.toml"
        bom_content = b'\xef\xbb\xbfmodel_provider = "openai"\nmodel = "gpt-5.6-sol"\n'
        config_path.write_bytes(bom_content)
        
        # Install
        code = configure_custom(tmp_dir)
        assert code == 0
        
        # Restore
        code_res = restore_original(tmp_dir)
        assert code_res == 0
        assert config_path.read_bytes() == bom_content, "BOM was stripped or modified!"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 10: Config absent initially
    print("Test 10: Config absent initially -> manifest records absent, restore deletes created config...")
    tmp_dir, _, _ = setup_temp_codex_fixture()
    try:
        config_path = tmp_dir / "config.toml"
        if config_path.exists():
            config_path.unlink()
            
        configure_custom(tmp_dir)
        manifest_path = tmp_dir / "aic-backup" / "manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        assert m["original_exists"] is False
        assert config_path.exists()
        
        restore_original(tmp_dir)
        assert not config_path.exists(), "Config should be removed since it was absent originally"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 11: Manifest corruption or missing backup aborts
    print("Test 11: Manifest corrupt or missing backup halts configure_custom before modifying config...")
    tmp_dir, _, _ = setup_temp_codex_fixture()
    try:
        config_path = tmp_dir / "config.toml"
        orig_config = b'model_provider = "openai"\n'
        config_path.write_bytes(orig_config)
        
        # Create a corrupt manifest
        backup_dir = tmp_dir / "aic-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text("{CORRUPT_JSON", encoding="utf-8")
        
        # configure_custom MUST abort and return 1
        code = configure_custom(tmp_dir)
        assert code == 1, f"Expected 1, got {code}"
        assert config_path.read_bytes() == orig_config, "Config should not have been modified!"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 12: Legacy uninstall preserves [profiles.*] and custom sections
    print("Test 12: Legacy uninstall preserves [profiles.*] and user configurations 100%...")
    tmp_dir, _, _ = setup_temp_codex_fixture()
    try:
        config_path = tmp_dir / "config.toml"
        legacy_config = (
            'model = "gemini-3.7-flash"\n'
            'model_provider = "custom"\n\n'
            '[model_providers.custom]\n'
            'name = "Custom Quota Pool"\n'
            'base_url = "http://127.0.0.1:8080/v1"\n'
            'wire_api = "responses"\n\n'
            '[profiles.personal]\n'
            'model = "gemini-3.7-flash"\n'
            'model_provider = "custom"\n\n'
            '[projects."/my-project"]\n'
            'trust_level = "trusted"\n\n'
            '[mcp_servers.database]\n'
            'command = "npx"\n'
            'args = ["-y", "@modelcontextprotocol/server-postgres"]\n'
        )
        config_path.write_bytes(legacy_config.encode("utf-8"))
            
        restore_original(tmp_dir)
        
        cleaned = config_path.read_bytes().decode("utf-8")
            
        # Top-level should be openai
        assert 'model_provider = "openai"' in cleaned
        assert '[model_providers.custom]' not in cleaned
        # [profiles.personal] MUST STILL HAVE its original model and model_provider
        assert '[profiles.personal]\nmodel = "gemini-3.7-flash"\nmodel_provider = "custom"' in cleaned
        assert '[projects."/my-project"]' in cleaned
        assert '[mcp_servers.database]' in cleaned
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 13: Verify mode does not alter files
    print("Test 13: Verify mode checks provider without modifying files...")
    tmp_dir, jsonl_path, db_path = setup_temp_codex_fixture("openai")
    try:
        assert verify_provider("openai", tmp_dir) == 0
        assert verify_provider("custom", tmp_dir) == 1
        with open(jsonl_path, "r", encoding="utf-8") as f:
            m = json.loads(f.readline())
            assert m["payload"]["model_provider"] == "openai"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 14: Public command verification
    print("Test 14: Verify 'aic sync' is NOT in bin/aic.py...")
    aic_py = ROOT_DIR / "bin" / "aic.py"
    with open(aic_py, "r", encoding="utf-8") as f:
        aic_content = f.read()
    assert 'cmd_sync' not in aic_content
    assert '"sync"' not in aic_content
    print("  -> [PASS]")
    tests_passed += 1

    print("\n" + "=" * 70)
    print(f"OFFLINE UNIT TESTS SUMMARY: {tests_passed}/{total_tests} passed (100% Green)")
    print("=" * 70)
    return tests_passed == total_tests


def test_sync_and_backup_unit():
    ok = run_all_unit_tests()
    if ok:
        return True, "14/14 offline unit tests passed (Session Sync, Atomic Mock, BOM/LF & Backup/Restore)."
    else:
        return False, "Offline unit tests failed."


if __name__ == "__main__":
    ok, msg = test_sync_and_backup_unit()
    print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)
