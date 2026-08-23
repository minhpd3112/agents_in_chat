#!/usr/bin/env python3
# ==============================================================================
#  backup_auths.py - Atomic Auto-Backup & Recovery for OAuth token files
#  Event-driven (lifecycle hooks only) -> Zero-RAM overhead, no daemon.
#
#  Usage:
#    python scripts/backup_auths.py backup   # Snapshot valid JSON tokens
#    python scripts/backup_auths.py restore  # Repair corrupt/missing from backup
#    python scripts/backup_auths.py verify   # Health report of auths/
# ==============================================================================

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
AUTHS_DIR = ROOT_DIR / "auths"
BACKUP_DIR = ROOT_DIR / "auths_backup"


def is_valid_json_file(path: Path) -> bool:
    """A token file is healthy iff: >0 bytes, no NUL bytes, parses as JSON."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        raw = path.read_bytes()
        if b"\x00" in raw:
            return False
        text = raw.decode("utf-8", errors="strict")
        json.loads(text)
        return True
    except (OSError, ValueError, UnicodeDecodeError):
        return False


def atomic_write(target: Path, data: bytes) -> bool:
    """Write bytes via temp file + os.replace to guarantee atomicity."""
    tmp_path = target.with_name(target.name + ".tmp_backup")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(target))
        return True
    except OSError as e:
        print(f"[ERROR] Atomic write failed for {target.name}: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def cmd_backup(auths_dir: Path = AUTHS_DIR, backup_dir: Path = BACKUP_DIR) -> int:
    """Copy only 100%-valid JSON files into the backup dir (atomic copy)."""
    if not auths_dir.is_dir():
        print(f"[SKIP] Thu muc {auths_dir} khong ton tai.")
        return 0
    backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up = skipped = 0
    for src in sorted(auths_dir.glob("*.json")):
        if not is_valid_json_file(src):
            print(f"[SKIP] Bo qua file hong/khong hop le: {src.name}")
            skipped += 1
            continue
        dst = backup_dir / src.name
        data = src.read_bytes()
        if dst.exists() and dst.read_bytes() == data:
            backed_up += 1  # already identical -> idempotent no-op
            continue
        if atomic_write(dst, data):
            backed_up += 1

    print(f"[OK] Backup hoan tat: {backed_up} file hop le trong '{backup_dir.name}/', bo qua {skipped} file rac.")
    return 0


def cmd_restore(auths_dir: Path = AUTHS_DIR, backup_dir: Path = BACKUP_DIR) -> int:
    """Repair every corrupt (NUL-byte/0-byte/bad JSON) or missing token file."""
    if not auths_dir.is_dir():
        print(f"[SKIP] Thu muc {auths_dir} khong ton tai.")
        return 0
    if not backup_dir.is_dir():
        print("[WARN] Khong co thu muc backup nao de phuc hoi.")
        return 0

    repaired = healthy = missing_backup = 0
    for live in sorted(auths_dir.glob("*.json")):
        if is_valid_json_file(live):
            healthy += 1
            continue
        snap = backup_dir / live.name
        if is_valid_json_file(snap):
            if atomic_write(live, snap.read_bytes()):
                print(f"[FIX] Da phuc hoi file hong tu backup: {live.name}")
                repaired += 1
        else:
            print(f"[WARN] File hong nhung khong co ban backup hop le: {live.name}")
            missing_backup += 1

    # Files lost entirely from auths/ but present & valid in backup/
    restored_missing = 0
    for snap in sorted(backup_dir.glob("*.json")):
        if not is_valid_json_file(snap):
            continue
        live = auths_dir / snap.name
        if live.exists():
            continue
        if atomic_write(live, snap.read_bytes()):
            print(f"[FIX] Da khoi phuc file bi mat tu backup: {snap.name}")
            restored_missing += 1

    print(
        f"[OK] Restore hoan tat: {healthy} file khoe, {repaired} file da sua, "
        f"{restored_missing} file da khoi phuc lai, {missing_backup} file khong the cuu."
    )
    return 0


def cmd_verify(auths_dir: Path = AUTHS_DIR) -> int:
    """Health report: count valid vs corrupt token files."""
    if not auths_dir.is_dir():
        print("[INFO] Chua co thu muc auths/.")
        return 0
    files = sorted(auths_dir.glob("*.json"))
    valid, corrupt = [], []
    for f in files:
        (valid if is_valid_json_file(f) else corrupt).append(f)

    print("=" * 60)
    print(f"  BAO CAO SUC KHOE TOKEN ({auths_dir.name}/)")
    print("=" * 60)
    print(f"Tong so file .json : {len(files)}")
    print(f"Hop le (JSON OK)   : {len(valid)}")
    for f in valid:
        print(f"  [OK]   {f.name}")
    print(f"Hong (rac/0-byte/NUL): {len(corrupt)}")
    for f in corrupt:
        print(f"  [BAD]  {f.name}")
    print("=" * 60)
    return 0 if not corrupt else 1


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1 or argv[0] not in ("backup", "restore", "verify"):
        print(__doc__)
        return 2
    action = argv[0]
    if action == "backup":
        return cmd_backup()
    if action == "restore":
        return cmd_restore()
    return cmd_verify()


if __name__ == "__main__":
    sys.exit(main())
