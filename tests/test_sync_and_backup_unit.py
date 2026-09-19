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
BIN_DIR = ROOT_DIR / "bin"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(BIN_DIR))

from sync_sessions import (
    sync_provider,
    verify_provider,
    sanitize_session_item,
    sanitize_instruction_text,
    has_unsanitized_fingerprint,
    realign_forked_lineages,
)
from configure_codex_toml import configure_custom, restore_original, ensure_backup, compute_sha256_bytes, compute_sha256_file
from config_manager import is_legacy_aic_instruction
from instruction_compat import verify_instruction_template
from session_store import (
    find_ordinal_byte_offset,
    verify_lineage_integrity,
    realign_forked_lineages as store_realign_lineages,
)
from aic import cmd_repair, cmd_repair_antigravity


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
    total_tests = 23

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

    # Test 15: Sanitize synthetic carrier reasoning tokens (cpa-) when syncing to openai
    print("Test 15: Sanitize synthetic carrier reasoning tokens (cpa-) on sync to openai...")
    tmp_dir, jsonl_path, _ = setup_temp_codex_fixture("custom")
    try:
        # Append synthetic carrier reasoning item and normal user/assistant items
        with open(jsonl_path, "a", encoding="utf-8") as f:
            carrier_item = {
                "type": "response_item",
                "payload": {
                    "type": "reasoning",
                    "id": "rs_resp_test_123_detached_before_0",
                    "encrypted_content": "cpa-gemini-responses-carrier-v1:next:function:XYZ"
                }
            }
            real_item = {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Follow up question"}]
                }
            }
            f.write(json.dumps(carrier_item) + "\n")
            f.write(json.dumps(real_item) + "\n")

        # Sync to openai
        code = sync_provider("openai", tmp_dir)
        assert code == 0
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Verify provider changed
        assert json.loads(lines[0])["payload"]["model_provider"] == "openai"
        # Verify cpa- carrier item was stripped
        assert not any("cpa-gemini" in l for l in lines)
        assert not any("rs_resp_test_123" in l for l in lines)
        # Verify normal user item was preserved
        assert any("Follow up question" in l for l in lines)
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 16: Lineage Re-alignment for Forked sessions
    print("Test 16: Re-align forked lineage cutoff offset when ancestor rollout shrinks during sanitization...")
    tmp_dir, _, _ = setup_temp_codex_fixture("custom")
    try:
        sessions_dir = tmp_dir / "sessions" / "2026-08"
        parent_file = sessions_dir / "parent.jsonl"
        with open(parent_file, "w", encoding="utf-8") as f:
            p_meta = {"type": "session_meta", "payload": {"id": "p1", "session_id": "p1", "model_provider": "custom"}}
            f.write(json.dumps(p_meta) + "\n")
            f.write(json.dumps({"type": "message", "ordinal": 1, "role": "user", "content": "Question"}) + "\n")
            f.write(json.dumps({"type": "response_item", "ordinal": 2, "payload": {"type": "reasoning", "encrypted_content": "cpa-gemini-carrier-payload-that-takes-bytes"}}) + "\n")
            f.write(json.dumps({"type": "response_item", "ordinal": 3, "payload": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "Answer"}]}}) + "\n")

        old_parent_size = os.path.getsize(parent_file)

        # Create child forked from parent at the end of parent
        child_file = sessions_dir / "child.jsonl"
        with open(child_file, "w", encoding="utf-8") as f:
            c_meta = {
                "type": "session_meta",
                "payload": {
                    "id": "c1",
                    "session_id": "c1",
                    "model_provider": "custom",
                    "history_base": {
                        "thread_id": "p1",
                        "end_ordinal_exclusive": 4,
                        "end_byte_offset": old_parent_size
                    }
                }
            }
            f.write(json.dumps(c_meta) + "\n")
            f.write(json.dumps({"type": "message", "ordinal": 4, "role": "user", "content": "Child follow-up"}) + "\n")

        # Sync to openai: parent should lose cpa- carrier lines and shrink
        code = sync_provider("openai", tmp_dir)
        assert code == 0, f"Expected 0, got {code}"

        new_parent_size = os.path.getsize(parent_file)
        assert new_parent_size < old_parent_size, "Parent should have shrunk after dropping cpa- carrier"

        with open(child_file, "r", encoding="utf-8") as f:
            c_lines = f.readlines()
        c_meta_new = json.loads(c_lines[0])
        new_cutoff = c_meta_new["payload"]["history_base"]["end_byte_offset"]

        assert new_cutoff == new_parent_size, f"Child cutoff ({new_cutoff}) must match new parent size ({new_parent_size})"
        assert new_cutoff <= new_parent_size, "Cutoff must not be past source rollout"

        # Verification must pass with zero issues
        verify_code = verify_provider("openai", tmp_dir)
        assert verify_code == 0, f"Verification failed with code {verify_code}"

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 17: Structured JSON Sanitization Unit Tests (preserves user/assistant/tool messages)
    print("Test 17: Structured JSON sanitization preserves user/assistant/tool messages and cleans developer <model_switch>...")
    
    # 17a. Developer <model_switch> with variant 1 (coding agent)
    dev_item_1 = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "<model_switch>\nThe user was previously using a different model. You are Codex, a coding agent based on GPT-5.\n</model_switch>"}]
        }
    }
    san_item, changed = sanitize_session_item(dev_item_1)
    assert changed is True
    assert "You are Codex, an expert coding agent." in san_item["payload"]["content"][0]["text"]
    assert "based on GPT-5" not in san_item["payload"]["content"][0]["text"]

    # 17b. Developer <model_switch> with variant 2 (agent) and string content
    dev_item_2 = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": "<model_switch>You are Codex, an agent based on GPT-5.</model_switch>"
        }
    }
    san_item, changed = sanitize_session_item(dev_item_2)
    assert changed is True
    assert san_item["payload"]["content"] == "<model_switch>You are Codex, an expert coding agent.</model_switch>"

    # 17c. Developer <model_switch> with generic based on GPT-5
    dev_item_3 = {
        "type": "message",
        "role": "developer",
        "content": "<model_switch>Context switch for system based on GPT-5.</model_switch>"
    }
    san_item, changed = sanitize_session_item(dev_item_3)
    assert changed is True
    assert "based on GPT-5" not in san_item["content"]
    assert "an expert coding agent" in san_item["content"]

    # 17d. Top-level instructions in turn_context
    turn_ctx = {
        "type": "turn_context",
        "payload": {
            "instructions": "You are Codex, a coding agent based on GPT-5.",
            "cwd": "/workspace"
        }
    }
    san_item, changed = sanitize_session_item(turn_ctx)
    assert changed is True
    assert san_item["payload"]["instructions"] == "You are Codex, an expert coding agent."

    # 17e. Strict preservation: User message quoting 'based on GPT-5'
    user_item = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Is this model based on GPT-5?"}]
        }
    }
    san_item, changed = sanitize_session_item(user_item)
    assert changed is False
    assert san_item["payload"]["content"][0]["text"] == "Is this model based on GPT-5?"

    # 17f. Strict preservation: Assistant message quoting 'based on GPT-5'
    asst_item = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "I am not based on GPT-5."}]
        }
    }
    san_item, changed = sanitize_session_item(asst_item)
    assert changed is False
    assert san_item["payload"]["content"][0]["text"] == "I am not based on GPT-5."

    # 17g. Strict preservation: Tool call and output quoting 'based on GPT-5'
    tool_call = {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "call_id": "c1",
            "name": "grep",
            "arguments": '{"query": "based on GPT-5"}'
        }
    }
    san_item, changed = sanitize_session_item(tool_call)
    assert changed is False
    assert 'based on GPT-5' in san_item["payload"]["arguments"]

    # 17h. Idempotency: running sanitize_session_item twice on sanitized item produces zero changes
    san_again, changed_again = sanitize_session_item(san_item)
    assert changed_again is False

    print("  -> [PASS]")
    tests_passed += 1

    # Test 18: Full Session Sync with Mixed Content & verify_provider integration
    print("Test 18: Full session sync sanitizes developer messages, preserves user messages, and verify_provider succeeds...")
    tmp_dir, _, _ = setup_temp_codex_fixture("custom")
    try:
        sessions_dir = tmp_dir / "sessions" / "2026-08"
        mixed_file = sessions_dir / "mixed_session.jsonl"
        with open(mixed_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_meta", "payload": {"id": "m1", "session_id": "m1", "model_provider": "openai"}}) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "<model_switch>\nYou are Codex, a coding agent based on GPT-5.\n</model_switch>"}]
                }
            }) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Can you explain systems based on GPT-5?"}]
                }
            }) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Systems based on GPT-5 use advanced reasoning."}]
                }
            }) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": "Found match: based on GPT-5"
                }
            }) + "\n")

        # Sync to custom
        sync_rc = sync_provider("custom", tmp_dir)
        assert sync_rc == 0, f"Expected 0, got {sync_rc}"

        with open(mixed_file, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]

        # Verify session_meta updated
        assert lines[0]["payload"]["model_provider"] == "custom"
        # Verify developer message sanitized
        dev_text = lines[1]["payload"]["content"][0]["text"]
        assert "You are Codex, an expert coding agent." in dev_text
        assert "based on GPT-5" not in dev_text
        # Verify user message preserved 100%
        user_text = lines[2]["payload"]["content"][0]["text"]
        assert user_text == "Can you explain systems based on GPT-5?"
        # Verify assistant message preserved 100%
        asst_text = lines[3]["payload"]["content"][0]["text"]
        assert asst_text == "Systems based on GPT-5 use advanced reasoning."
        # Verify tool output preserved 100%
        tool_out = lines[4]["payload"]["output"]
        assert tool_out == "Found match: based on GPT-5"

        # Verification must succeed completely
        v_rc = verify_provider("custom", tmp_dir, check_instructions=True)
        assert v_rc == 0, f"Verification failed with code {v_rc}"

        # Inject un-sanitized developer message -> verification must catch it!
        with open(mixed_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": "<model_switch>You are Codex, an agent based on GPT-5.</model_switch>"
                }
            }) + "\n")
        v_rc_injected = verify_provider("custom", tmp_dir, check_instructions=True)
        assert v_rc_injected == 1, "Verification must fail when developer instructions contain based on GPT-5"

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 19: cmd_repair auto-terminates running codex, reconciles cache and history in isolation
    print("Test 19: cmd_repair auto-terminates running codex, reconciles cache and history in isolation...")
    assert cmd_repair_antigravity is cmd_repair, "cmd_repair_antigravity must be an alias for cmd_repair"
    tmp_dir, _, _ = setup_temp_codex_fixture("custom")
    try:
        # 19a. Auto-terminate running codex and proceed with repair seamlessly
        os.environ["AIC_TEST_MODE"] = "1"
        os.environ["AIC_MOCK_CODEX_RUNNING"] = "1"
        try:
            rc = cmd_repair(tmp_dir)
            assert rc == 0, f"Expected 0 (auto-terminate & repair), got {rc}"
        finally:
            os.environ.pop("AIC_MOCK_CODEX_RUNNING", None)

        # 19b. Success path: codex is not running
        # Put an un-sanitized developer message in sessions
        sessions_dir = tmp_dir / "sessions" / "2026-08"
        sess_file = sessions_dir / "repair_test.jsonl"
        with open(sess_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_meta", "payload": {"id": "r1", "session_id": "r1", "model_provider": "custom"}}) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "<model_switch>You are Codex, a coding agent based on GPT-5.</model_switch>"}]
                }
            }) + "\n")
            f.write(json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hello User query"}]
                }
            }) + "\n")

        repair_rc = cmd_repair(tmp_dir)
        assert repair_rc == 0, f"cmd_repair failed with code {repair_rc}"

        # Verify models_cache.json was created and locked Read-Only
        cache_file = tmp_dir / "models_cache.json"
        assert cache_file.exists(), "models_cache.json must exist"
        if sys.platform == "win32":
            import stat
            is_ro = bool(os.stat(cache_file).st_mode & stat.S_IREAD) and not bool(os.stat(cache_file).st_mode & stat.S_IWRITE)
            assert is_ro, "models_cache.json must be read-only"

        # Verify developer message was sanitized
        with open(sess_file, "r", encoding="utf-8") as f:
            r_lines = [json.loads(l) for l in f if l.strip()]
        assert "You are Codex, an expert coding agent." in r_lines[1]["payload"]["content"][0]["text"]
        assert "based on GPT-5" not in r_lines[1]["payload"]["content"][0]["text"]
        assert r_lines[2]["payload"]["content"][0]["text"] == "Hello User query"

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 20: Instruction & Config edge cases (no top-level instruction, legacy cleanup, custom kept, template verification)
    print("Test 20: Instruction & Config edge cases (no top-level, cleanup legacy, preserve custom & templates)...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="aic_test_cfg_"))
    try:
        cfg_path = tmp_dir / "config.toml"

        # 20a. Fresh install has NO top-level instructions key
        rc = configure_custom(tmp_dir)
        assert rc == 0
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        top_inst_lines = [l for l in lines if l.strip().startswith("instructions")]
        assert len(top_inst_lines) == 0, f"Expected 0 top-level instructions, got {top_inst_lines}"

        # 20b. Legacy AIC instruction is stripped on configure_custom
        shutil.rmtree(tmp_dir / "aic-backup", ignore_errors=True)
        cfg_path.write_text(
            'model = "gemini-3.8-flash"\n'
            'instructions = "You are Codex, an expert coding agent."\n'
            'model_instructions_file = "my/file.md"\n'
            '[profiles.test]\nname = "test"\n',
            encoding="utf-8"
        )
        rc = configure_custom(tmp_dir)
        assert rc == 0
        content = cfg_path.read_text(encoding="utf-8")
        assert 'instructions = "You are Codex, an expert coding agent."' not in content
        assert 'model_instructions_file = "my/file.md"' in content
        assert '[profiles.test]' in content

        # 20c. User's custom instruction is preserved
        shutil.rmtree(tmp_dir / "aic-backup", ignore_errors=True)
        cfg_path.write_text(
            'model = "gemini-3.8-flash"\n'
            'instructions = "Always speak like a pirate."\n'
            '[windows]\nsandbox = "elevated"\n',
            encoding="utf-8"
        )
        rc = configure_custom(tmp_dir)
        assert rc == 0
        content = cfg_path.read_text(encoding="utf-8")
        assert 'instructions = "Always speak like a pirate."' in content

        # 20d. is_legacy_aic_instruction helper validation
        assert is_legacy_aic_instruction('instructions = "You are Codex, an expert coding agent."') is True
        assert is_legacy_aic_instruction("instructions = 'You are Codex, an expert coding agent.'") is True
        assert is_legacy_aic_instruction('instructions = "Custom user prompt"') is False
        assert is_legacy_aic_instruction('model_instructions_file = "You are Codex, an expert coding agent."') is False

        # 20e. verify_instruction_template validation
        template_file = ROOT_DIR / "docs" / "models_cache_template.json"
        if template_file.exists():
            tmpl_json = json.loads(template_file.read_text(encoding="utf-8"))
            for m in tmpl_json.get("models", []):
                t_str = m.get("model_messages", {}).get("instructions_template")
                if t_str:
                    ok, msg = verify_instruction_template(t_str)
                    assert ok is True, f"Template for {m.get('slug')} failed verification: {msg}"

        assert verify_instruction_template("")[0] is False
        assert verify_instruction_template("short")[0] is False
        assert verify_instruction_template("# Personality\n## Writing style\n# Rules for getting work done\n## Final answer\n" + "x" * 1200 + "\nbased on GPT-5")[0] is False

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 21: cmd_repair failure injection and abort before mutation
    print("Test 21: cmd_repair failure injection (kill fail aborts before mutation, step failures)...")
    tmp_dir, _, _ = setup_temp_codex_fixture("custom")
    try:
        os.environ["AIC_TEST_MODE"] = "1"

        # 21a. Kill failure stops repair BEFORE models_cache.json or config is touched
        os.environ["AIC_MOCK_CODEX_RUNNING"] = "1"
        os.environ["AIC_MOCK_KILL_FAIL"] = "1"
        cache_file = tmp_dir / "models_cache.json"
        if cache_file.exists():
            cache_file.unlink()
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 (kill fail), got {rc}"
        assert not cache_file.exists(), "models_cache.json must NOT be created when kill fails"
        os.environ.pop("AIC_MOCK_CODEX_RUNNING", None)
        os.environ.pop("AIC_MOCK_KILL_FAIL", None)

        # 21b. Injected failure at repair-configure
        os.environ["AIC_FAIL_STEP"] = "repair-configure"
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 at repair-configure, got {rc}"

        # 21c. Injected failure at repair-models-cache
        os.environ["AIC_FAIL_STEP"] = "repair-models-cache"
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 at repair-models-cache, got {rc}"

        # 21d. Injected failure at repair-sync
        os.environ["AIC_FAIL_STEP"] = "repair-sync"
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 at repair-sync, got {rc}"

        # 21e. Injected failure at repair-sqlite-cache
        os.environ["AIC_FAIL_STEP"] = "repair-sqlite-cache"
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 at repair-sqlite-cache, got {rc}"

        # 21f. Injected failure at repair-verify
        os.environ["AIC_FAIL_STEP"] = "repair-verify"
        rc = cmd_repair(tmp_dir)
        assert rc == 1, f"Expected 1 at repair-verify, got {rc}"
        os.environ.pop("AIC_FAIL_STEP", None)

        # 21g. Missing config.toml is automatically created and configured for custom
        cfg_file = tmp_dir / "config.toml"
        if cfg_file.exists():
            cfg_file.unlink()
        shutil.rmtree(tmp_dir / "aic-backup", ignore_errors=True)
        rc = cmd_repair(tmp_dir)
        assert rc == 0, f"Expected 0 for missing config repair, got {rc}"
        assert cfg_file.exists(), "config.toml should have been auto-created"
        assert 'model_provider = "custom"' in cfg_file.read_text(encoding="utf-8")

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        os.environ.pop("AIC_FAIL_STEP", None)
        os.environ.pop("AIC_MOCK_CODEX_RUNNING", None)
        os.environ.pop("AIC_MOCK_KILL_FAIL", None)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 22: Sanitizer scope boundaries & non-switch developer messages
    print("Test 22: Sanitizer scope boundaries & non-switch developer message preservation...")
    # 22a. Developer message without <model_switch> is NOT modified even if containing 'based on GPT-5'
    dev_msg_normal = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "This instruction is based on GPT-5 technical report notes."}]
        }
    }
    san_item, changed = sanitize_session_item(dev_msg_normal)
    assert changed is False
    assert san_item["payload"]["content"][0]["text"] == "This instruction is based on GPT-5 technical report notes."
    assert has_unsanitized_fingerprint(dev_msg_normal) is False

    # 22b. Developer message with <model_switch> and suffix: switch is sanitized, suffix is preserved intact
    suffix_text = "\n\n# Rules for getting work done\n1. Do not break existing tests."
    dev_msg_switch = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": f"<model_switch>You are Codex, a coding agent based on GPT-5.</model_switch>{suffix_text}"}]
        }
    }
    san_item, changed = sanitize_session_item(dev_msg_switch)
    assert changed is True
    res_text = san_item["payload"]["content"][0]["text"]
    assert "<model_switch>You are Codex, an expert coding agent.</model_switch>" in res_text
    assert suffix_text in res_text
    assert has_unsanitized_fingerprint(san_item) is False

    # 22c. User & assistant messages containing competitor name are untouched & not flagged
    user_msg = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Is Codex based on GPT-5?"}]
        }
    }
    assert sanitize_session_item(user_msg)[1] is False
    assert has_unsanitized_fingerprint(user_msg) is False

    asst_msg = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Yes, originally based on GPT-5."}]
        }
    }
    assert sanitize_session_item(asst_msg)[1] is False
    assert has_unsanitized_fingerprint(asst_msg) is False

    print("  -> [PASS]")
    tests_passed += 1

    # Test 23: Lineage mid-parent fork drift & ordinal-based verification/realignment
    print("Test 23: Lineage mid-parent fork drift & ordinal-based realignment...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="aic_test_lineage_"))
    sessions_dir = tmp_dir / "sessions" / "2026-08"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    try:
        parent_file = sessions_dir / "parent.jsonl"
        child_file = sessions_dir / "child.jsonl"

        # Parent has 5 items with ordinals 0, 1, 2, 3, 4
        p_lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "parent_thread", "model_provider": "custom"}}) + "\n",
            json.dumps({"type": "turn_context", "ordinal": 0, "payload": {"turn": 0, "text": "Turn 0 context"}}) + "\n",
            json.dumps({"type": "response_item", "ordinal": 1, "payload": {"carrier": "cpa-gemini-carrier-token-long-blob-padding-xyz"}}) + "\n",
            json.dumps({"type": "turn_context", "ordinal": 2, "payload": {"turn": 2, "text": "Turn 2 context"}}) + "\n",
            json.dumps({"type": "response_item", "ordinal": 3, "payload": {"text": "Turn 3 assistant"}}) + "\n",
            json.dumps({"type": "response_item", "ordinal": 4, "payload": {"text": "Turn 4 assistant"}}) + "\n",
        ]
        parent_file.write_bytes("".join(p_lines).encode("utf-8"))

        # Fork point: child forked at ordinal 2 (end_ordinal_exclusive = 2)
        # Expected cutoff is start of line with ordinal 2 (i.e. length of lines 0, 1, 2)
        expected_cutoff = len("".join(p_lines[:3]).encode("utf-8"))
        assert find_ordinal_byte_offset(str(parent_file), 2) == expected_cutoff

        # Child initially recorded this cutoff
        c_lines = [
            json.dumps({
                "type": "session_meta",
                "payload": {
                    "id": "child_thread",
                    "model_provider": "custom",
                    "history_base": {
                        "thread_id": "parent_thread",
                        "end_byte_offset": expected_cutoff,
                        "end_ordinal_exclusive": 2
                    }
                }
            }) + "\n",
            json.dumps({"type": "turn_context", "ordinal": 0, "payload": {"turn": 0, "text": "Child turn 0"}}) + "\n",
        ]
        child_file.write_bytes("".join(c_lines).encode("utf-8"))

        # Initial check: integrity is valid
        issues = verify_lineage_integrity(sessions_dir)
        assert len(issues) == 0, f"Expected 0 issues initially, got {issues}"

        # Now simulate parent turn 1 shrinking (e.g. carrier stripped by 40 bytes)
        shrunk_line_2 = json.dumps({"type": "response_item", "ordinal": 1, "payload": {"clean": "ok"}}) + "\n"
        shrunk_p_lines = [p_lines[0], p_lines[1], shrunk_line_2, p_lines[3], p_lines[4], p_lines[5]]
        parent_file.write_bytes("".join(shrunk_p_lines).encode("utf-8"))

        # Crucial check: parent_size is STILL > child's recorded cutoff!
        new_parent_size = parent_file.stat().st_size
        assert expected_cutoff < new_parent_size, "Parent size must still exceed old cutoff"

        # But the cutoff is now DESYNCHRONIZED from ordinal 2!
        # verify_lineage_integrity must catch this drift!
        drift_issues = verify_lineage_integrity(sessions_dir)
        assert len(drift_issues) == 1, f"Expected 1 drift issue, got {drift_issues}"
        assert "differs from expected ordinal offset" in drift_issues[0]

        # realign_forked_lineages must repair the offset
        realigned, errs = store_realign_lineages(sessions_dir)
        assert realigned == 1
        assert errs == 0

        # Post-realignment check: child end_byte_offset matches find_ordinal_byte_offset
        new_expected = find_ordinal_byte_offset(str(parent_file), 2)
        child_meta = json.loads(child_file.read_text(encoding="utf-8").splitlines()[0])
        assert child_meta["payload"]["history_base"]["end_byte_offset"] == new_expected

        # Post-realignment verification: 0 issues!
        clean_issues = verify_lineage_integrity(sessions_dir)
        assert len(clean_issues) == 0, f"Expected 0 issues after realignment, got {clean_issues}"

        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print(f"OFFLINE UNIT TESTS SUMMARY: {tests_passed}/{total_tests} passed (100% Green)")
    print("=" * 70)
    return tests_passed == total_tests


def test_sync_and_backup_unit():
    ok = run_all_unit_tests()
    if ok:
        return True, "23/23 offline unit tests passed (Session Sync, Carrier Sanitizer, Lineage Re-align, Structured JSON Sanitization, Anti-filter Verification, Repair Tool, Instruction Compat, Failure Injections & Lineage Ordinal Drift)."
    else:
        return False, "Offline unit tests failed."


if __name__ == "__main__":
    ok, msg = test_sync_and_backup_unit()
    print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)
