import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from backup_auths import is_valid_json_file, cmd_backup, cmd_restore, cmd_verify


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

    # 2. Fault injection: NUL-byte corruption restored byte-exact
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        victim = auths / "antigravity-victim.json"
        token(victim, sample_payload("victim"))
        assert run(cmd_backup, auths, backup) == 0
        original = victim.read_bytes()

        victim.write_bytes(b"\x00" * len(original))
        assert run(cmd_restore, auths, backup) == 0
        assert victim.read_bytes() == original, "restore is not byte-exact"
        assert is_valid_json_file(victim)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 3. Missing file in auths/ is recovered from backup
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        lost = auths / "codex-lost-account.json"
        token(lost, {"type": "codex", "email": "lost"})
        assert run(cmd_backup, auths, backup) == 0

        lost.unlink()
        assert run(cmd_restore, auths, backup) == 0
        assert lost.exists() and is_valid_json_file(lost)
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

    # 5. Restore never overwrites a healthy live token (newer state wins)
    tmp_dir, auths, backup = setup_auth_fixture()
    try:
        fresh = auths / "antigravity-fresh.json"
        token(fresh, sample_payload("fresh"))
        assert run(cmd_backup, auths, backup) == 0

        refreshed = sample_payload("fresh")
        refreshed["access_token"] = "brand-new-token-after-refresh"
        token(fresh, refreshed)

        assert run(cmd_restore, auths, backup) == 0
        current = json.loads(fresh.read_text(encoding="utf-8"))
        assert current["access_token"] == "brand-new-token-after-refresh"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 6. Verify exit codes + production auths/ must be fully healthy
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

    return True, "6/6 scenarios passed (valid-only snapshot, byte-exact recovery, missing-file recovery, idempotency, non-destructive restore, verify)."


if __name__ == "__main__":
    ok, msg = test_auth_backup()
    print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)
