import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from backup_auths import is_valid_json_file, cmd_backup, cmd_restore, cmd_verify


def setup_auth_fixture():
    tmp_dir = Path(tempfile.mkdtemp(prefix="aic_test_auth_backup_"))
    auths = tmp_dir / "auths"
    backup = tmp_dir / "auths_backup"
    auths.mkdir(parents=True)
    return tmp_dir, auths, backup


def write_token(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sample_payload(email: str) -> dict:
    return {
        "type": "antigravity",
        "email": email,
        "refresh_token": f"dummy-refresh-{email}",
        "access_token": f"dummy-access-{email}",
    }


def run_all_unit_tests() -> bool:
    print("=" * 70)
    print("  RUNNING UNIT & REGRESSION TESTS FOR AUTH ATOMIC BACKUP/RECOVERY")
    print("=" * 70)

    tests_passed = 0
    total_tests = 6

    # Test 1: Backup only stores valid JSON, rejects NUL-byte / bad syntax files
    print("Test 1: Backup stores only 100%-valid JSON (rejects NUL-byte & malformed)...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        write_token(auths / "antigravity-good@gmail.com.json", sample_payload("good"))
        (auths / "antigravity-nulled.json").write_bytes(b"\x00" * 512)
        (auths / "antigravity-bad.json").write_text("{INVALID JSON,,", encoding="utf-8")
        (auths / "antigravity-empty.json").write_bytes(b"")

        code = cmd_backup(auths, backup)
        assert code == 0

        backed = sorted(p.name for p in backup.glob("*.json"))
        assert backed == ["antigravity-good@gmail.com.json"], f"Unexpected backup contents: {backed}"
        assert not (backup / "antigravity-nulled.json").exists()
        assert not (backup / "antigravity-bad.json").exists()
        assert not (backup / "antigravity-empty.json").exists()
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 2: Fault injection - NUL-byte corruption restored byte-exact
    print("Test 2: NUL-byte fault injection on antigravity-*.json restores byte-exact...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        victim = auths / "antigravity-victim.json"
        write_token(victim, sample_payload("victim"))
        code = cmd_backup(auths, backup)
        assert code == 0
        original_bytes = (backup / victim.name).read_bytes()

        # Simulate sudden power loss: file becomes all NUL bytes
        victim.write_bytes(b"\x00" * len(original_bytes))
        assert b"\x00" in victim.read_bytes()

        code = cmd_restore(auths, backup)
        assert code == 0
        assert victim.read_bytes() == original_bytes, "Restore is NOT byte-exact!"
        assert json.loads(victim.read_text(encoding="utf-8"))["email"] == "victim"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 3: Missing file in auths/ is re-created from backup
    print("Test 3: Deleted token file in auths/ is auto-restored from backup...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        lost = auths / "codex-lost-account.json"
        write_token(lost, {"type": "codex", "email": "lost", "tokens": {"id_token": "x"}})
        code = cmd_backup(auths, backup)
        assert code == 0

        lost.unlink()
        assert not lost.exists()

        code = cmd_restore(auths, backup)
        assert code == 0
        assert lost.exists(), "Missing file was not recovered!"
        assert json.loads(lost.read_text(encoding="utf-8"))["email"] == "lost"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 4: Idempotency - repeated backup/restore cycles never corrupt data
    print("Test 4: Idempotency - repeated backup/restore cycles keep data stable...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        token = auths / "openai-compatible-opencode-zen.json"
        write_token(token, {"type": "openai-compatible", "key": "public"})
        golden = token.read_bytes()

        for _ in range(3):
            assert cmd_backup(auths, backup) == 0
            assert cmd_restore(auths, backup) == 0
            assert token.read_bytes() == golden, "Idempotency violated!"

        # Corrupt + restore must also be repeatable with identical result
        token.write_bytes(b"\x00" * 64)
        assert cmd_restore(auths, backup) == 0
        first_repair = token.read_bytes()
        assert first_repair == golden
        token.write_bytes(b"\x00" * 64)
        assert cmd_restore(auths, backup) == 0
        assert token.read_bytes() == first_repair
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 5: Healthy files are never touched by restore (no destructive overwrite)
    print("Test 5: Restore never touches healthy live tokens (newer state preserved)...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        fresh = auths / "antigravity-fresh.json"
        write_token(fresh, sample_payload("fresh"))
        cmd_backup(auths, backup)
        # Simulate proxy refreshing the token AFTER the last backup
        refreshed = sample_payload("fresh")
        refreshed["access_token"] = "brand-new-token-after-refresh"
        write_token(fresh, refreshed)

        assert cmd_restore(auths, backup) == 0
        current = json.loads(fresh.read_text(encoding="utf-8"))
        assert current["access_token"] == "brand-new-token-after-refresh", \
            "Restore overwrote a healthy newer token!"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test 6: Verify reports health correctly and returns proper exit codes
    print("Test 6: verify() reports valid/corrupt counts and exit codes...")
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        write_token(auths / "ok.json", sample_payload("ok"))
        (auths / "nulled.json").write_bytes(b"\x00" * 128)
        assert cmd_verify(auths) == 1  # corrupt present -> exit 1

        (auths / "nulled.json").unlink()
        assert cmd_verify(auths) == 0  # all healthy -> exit 0

        # Real repo sanity check: production auths/ must be fully healthy
        real_auths = ROOT_DIR / "auths"
        if real_auths.is_dir():
            for real_file in real_auths.glob("*.json"):
                assert is_valid_json_file(real_file), \
                    f"Production token corrupted and needs recovery: {real_file.name}"
        print("  -> [PASS]")
        tests_passed += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print(f"AUTH BACKUP/RECOVERY TESTS SUMMARY: {tests_passed}/{total_tests} passed (100% Green)")
    print("=" * 70)
    return tests_passed == total_tests


def test_auth_backup():
    ok = run_all_unit_tests()
    if ok:
        return True, "6/6 unit tests passed (Atomic Backup, NUL-byte Recovery, Missing File, Idempotency, Non-destructive Restore, Verify)."
    else:
        return False, "Auth backup/recovery tests failed."


if __name__ == "__main__":
    ok, msg = test_auth_backup()
    print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)
