import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from backup_auths import is_valid_json_file, atomic_write, cmd_backup, cmd_restore, cmd_verify


def setup_auth_fixture():
    tmp_dir = Path(tempfile.mkdtemp(prefix="aic_test_auth_backup_"))
    auths = tmp_dir / "auths"
    auths.mkdir(parents=True)
    return tmp_dir, auths, tmp_dir / "auths_backup"


def run(cmd, *args):
    with redirect_stderr(io.StringIO()):
        return cmd(*args)


def token(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sample_payload(email: str) -> dict:
    return {
        "type": "antigravity",
        "email": email,
        "refresh_token": f"dummy-refresh-{email}",
        "access_token": f"dummy-access-{email}",
    }


def test_auth_backup():
    # 1. Backup stores only valid JSON; rejects NUL-byte / malformed / empty files
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        token(auths / "antigravity-good@gmail.com.json", sample_payload("good"))
        (auths / "antigravity-nulled.json").write_bytes(b"\x00" * 512)
        (auths / "antigravity-bad.json").write_text("{INVALID JSON,,", encoding="utf-8")
        (auths / "antigravity-empty.json").write_bytes(b"")

        assert run(cmd_backup, auths, backup) == 0
        assert [p.name for p in backup.glob("*.json")] == ["antigravity-good@gmail.com.json"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 2. Fault injection: NUL-byte and 0-byte corruption restored byte-exact
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        victim1 = auths / "antigravity-victim1.json"
        victim2 = auths / "antigravity-victim2.json"
        token(victim1, sample_payload("victim1"))
        token(victim2, sample_payload("victim2"))
        assert run(cmd_backup, auths, backup) == 0
        original1 = victim1.read_bytes()
        original2 = victim2.read_bytes()

        victim1.write_bytes(b"\x00" * len(original1))
        victim2.write_bytes(b"")
        assert run(cmd_restore, auths, backup) == 0
        assert victim1.read_bytes() == original1, "restore of nulled file is not byte-exact"
        assert victim2.read_bytes() == original2, "restore of 0-byte file is not byte-exact"
        assert is_valid_json_file(victim1)
        assert is_valid_json_file(victim2)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 3. Missing/deleted file in auths/ is NOT recovered from backup (preserves backup)
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        lost = auths / "codex-lost-account.json"
        token(lost, {"type": "codex", "email": "lost"})
        assert run(cmd_backup, auths, backup) == 0
        assert (backup / lost.name).exists()

        lost.unlink()
        assert run(cmd_restore, auths, backup) == 0
        assert not lost.exists(), "Deleted account must not be recreated by restore"
        assert (backup / lost.name).exists(), "Backup of deleted account must be preserved"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 4. Idempotency: repeated cycles never mutate or corrupt data
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        live = auths / "openai-compatible-opencode-zen.json"
        token(live, {"type": "openai-compatible", "key": "public"})
        golden = live.read_bytes()

        for _ in range(3):
            assert run(cmd_backup, auths, backup) == 0
            assert run(cmd_restore, auths, backup) == 0
            assert live.read_bytes() == golden, "idempotency violated"

        for _ in range(2):
            live.write_bytes(b"\x00" * 64)
            assert run(cmd_restore, auths, backup) == 0
            assert live.read_bytes() == golden
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 5. Restore never overwrites a healthy live token (newer state wins, e.g. disabled=True)
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        fresh = auths / "antigravity-fresh.json"
        token(fresh, sample_payload("fresh"))
        assert run(cmd_backup, auths, backup) == 0

        refreshed = sample_payload("fresh")
        refreshed["access_token"] = "brand-new-token-after-refresh"
        token(fresh, refreshed)

        dis_file = auths / "antigravity-disabled.json"
        token(dis_file, {"type": "antigravity", "email": "dis@gmail.com", "disabled": False})
        assert run(cmd_backup, auths, backup) == 0
        token(dis_file, {"type": "antigravity", "email": "dis@gmail.com", "disabled": True})

        assert run(cmd_restore, auths, backup) == 0

        curr_fresh = json.loads(fresh.read_text(encoding="utf-8"))
        assert curr_fresh["access_token"] == "brand-new-token-after-refresh"

        curr_dis = json.loads(dis_file.read_text(encoding="utf-8"))
        assert curr_dis["disabled"] is True, "Live disabled=True must NOT be overwritten by backup disabled=False"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 6. Corrupt live file with missing or corrupt backup is preserved without damage
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        no_bak = auths / "no-bak.json"
        no_bak.write_bytes(b"{invalid-json-without-backup")

        corrupt_bak = auths / "bad-bak.json"
        corrupt_bak.write_bytes(b"{corrupt-live")
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "bad-bak.json").write_bytes(b"\x00" * 256)

        assert run(cmd_restore, auths, backup) == 0
        assert no_bak.read_bytes() == b"{invalid-json-without-backup"
        assert corrupt_bak.read_bytes() == b"{corrupt-live"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 7. Backup read error: returns 1, partial failure backs up remaining valid files
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        token(auths / "good.json", sample_payload("good"))
        token(auths / "unreadable.json", sample_payload("unreadable"))

        orig_read_bytes = Path.read_bytes

        def fake_read_bytes(self):
            if self.name == "unreadable.json":
                raise PermissionError("Simulated permission denied on read")
            return orig_read_bytes(self)

        with patch.object(Path, "read_bytes", fake_read_bytes):
            rc = run(cmd_backup, auths, backup)
            assert rc == 1, f"Expected backup to return 1 on read failure, got {rc}"

        assert (backup / "good.json").exists(), "Valid file should have been backed up"
        assert not (backup / "unreadable.json").exists(), "Unreadable file should not be in backup"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 8. Backup write error: returns 1, good backup untouched
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        token(auths / "target.json", sample_payload("target"))
        assert run(cmd_backup, auths, backup) == 0
        good_backup_bytes = (backup / "target.json").read_bytes()

        with patch("backup_auths.atomic_write", return_value=False):
            token(auths / "target.json", sample_payload("updated"))
            rc = run(cmd_backup, auths, backup)
            assert rc == 1, f"Expected backup to return 1 on write failure, got {rc}"

        assert (backup / "target.json").read_bytes() == good_backup_bytes

        dst = backup / "test_atomic.json"
        with patch("os.replace", side_effect=OSError("Simulated replace failure")):
            with redirect_stderr(io.StringIO()):
                write_ok = atomic_write(dst, b"test-data")
            assert write_ok is False
            assert not (backup / "test_atomic.json.tmp_backup").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 9. Empty auths/ directory returns 0
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        assert run(cmd_backup, auths, backup) == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 10. All files corrupt in auths/ (skips all, returns 0 when no I/O errors)
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        (auths / "bad1.json").write_bytes(b"")
        (auths / "bad2.json").write_bytes(b"\x00" * 64)
        (auths / "bad3.json").write_text("{bad", encoding="utf-8")
        assert run(cmd_backup, auths, backup) == 0
        assert len(list(backup.glob("*.json"))) == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 11. Verify exit codes + production auths/ must be fully healthy
    tmp_dir, auths, _ = setup_auth_fixture()
    try:
        token(auths / "ok.json", sample_payload("ok"))
        (auths / "nulled.json").write_bytes(b"\x00" * 128)
        assert run(cmd_verify, auths) == 1
        (auths / "nulled.json").unlink()
        assert run(cmd_verify, auths) == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    real_auths = ROOT_DIR / "auths"
    if real_auths.is_dir():
        corrupt = [f.name for f in real_auths.glob("*.json") if not is_valid_json_file(f)]
        assert not corrupt, f"production tokens need recovery: {corrupt}"

    return True, "11/11 scenarios passed (valid-only snapshot, byte-exact recovery, no-resurrection, idempotency, non-destructive restore, corrupt preserved, read error failure, write error failure, empty dir, all-corrupt dir, verify)."


if __name__ == "__main__":
    ok, msg = test_auth_backup()
    print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)
