#!/usr/bin/env python3
"""Atomic backup & recovery for OAuth token files in auths/.

Usage:
    python scripts/backup_auths.py backup   Snapshot valid tokens into auths_backup/
    python scripts/backup_auths.py restore  Repair corrupt/missing tokens from backup
    python scripts/backup_auths.py verify   Health report (exit 1 if anything is corrupt)

Status goes to stderr so stdout stays clean for scripting.
"""

import json
import os
import sys
from pathlib import Path

from log_utils import info, warn

ROOT_DIR = Path(__file__).resolve().parent.parent


def get_auths_dir() -> Path:
    if os.environ.get("AIC_TEST_MODE") == "1":
        override = os.environ.get("AIC_AUTHS_DIR")
        if not override:
            raise RuntimeError("AIC_TEST_MODE=1 requires AIC_AUTHS_DIR to be set")
        return Path(override)
    return ROOT_DIR / "auths"


def get_backup_dir() -> Path:
    if os.environ.get("AIC_TEST_MODE") == "1":
        override = os.environ.get("AIC_AUTHS_BACKUP_DIR")
        if not override:
            raise RuntimeError("AIC_TEST_MODE=1 requires AIC_AUTHS_BACKUP_DIR to be set")
        return Path(override)
    return ROOT_DIR / "auths_backup"


try:
    AUTHS_DIR = get_auths_dir()
except RuntimeError:
    AUTHS_DIR = None

try:
    BACKUP_DIR = get_backup_dir()
except RuntimeError:
    BACKUP_DIR = None


def is_valid_json_file(path: Path) -> bool:
    """A token file is healthy iff: >0 bytes, no NUL bytes, parses as JSON."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        raw = path.read_bytes()
        if b"\x00" in raw:
            return False
        json.loads(raw.decode("utf-8"))
        return True
    except (OSError, ValueError, UnicodeDecodeError):
        return False


def atomic_write(target: Path, data: bytes) -> bool:
    tmp = target.with_name(target.name + ".tmp_backup")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(target))
        return True
    except OSError as e:
        warn(f"could not write {target.name}: {e}")
        tmp.unlink(missing_ok=True)
        return False


def cmd_backup(auths_dir: Path = None, backup_dir: Path = None) -> int:
    """Snapshot only 100%-valid token files (atomic copy, idempotent)."""
    if auths_dir is None:
        auths_dir = get_auths_dir()
    if backup_dir is None:
        backup_dir = get_backup_dir()
    if not auths_dir.is_dir():
        info("No auth files to back up")
        return 0
    backup_dir.mkdir(parents=True, exist_ok=True)

    updated = kept = skipped = 0
    for src in sorted(auths_dir.glob("*.json")):
        if not is_valid_json_file(src):
            skipped += 1
            continue
        dst = backup_dir / src.name
        data = src.read_bytes()
        if dst.exists() and dst.read_bytes() == data:
            kept += 1
            continue
        if atomic_write(dst, data):
            updated += 1

    total = updated + kept
    if total == 0:
        warn("no valid auth files found")
    elif updated:
        info(f"Backed up {updated} auth file(s) to auths_backup/")
    else:
        info(f"Auth backup up to date ({total} file(s))")
    if skipped:
        warn(f"skipped {skipped} corrupt file(s)")
    return 0


def cmd_restore(auths_dir: Path = None, backup_dir: Path = None) -> int:
    """Repair every corrupt or missing token file from its backup."""
    if auths_dir is None:
        auths_dir = get_auths_dir()
    if backup_dir is None:
        backup_dir = get_backup_dir()
    if not auths_dir.is_dir() or not backup_dir.is_dir():
        return 0

    repaired = healthy = lost = 0
    for live in sorted(auths_dir.glob("*.json")):
        if is_valid_json_file(live):
            healthy += 1
            continue
        snap = backup_dir / live.name
        if is_valid_json_file(snap) and atomic_write(live, snap.read_bytes()):
            repaired += 1
        else:
            warn(f"no valid backup for {live.name}")
            lost += 1

    recovered = 0
    for snap in sorted(backup_dir.glob("*.json")):
        live = auths_dir / snap.name
        if not is_valid_json_file(snap) or live.exists():
            continue
        if atomic_write(live, snap.read_bytes()):
            recovered += 1

    fixed = repaired + recovered
    if fixed:
        info(f"Restored {fixed} auth file(s) from backup")
    elif lost == 0:
        info("All auth files are healthy")
    return 0


def cmd_verify(auths_dir: Path = None) -> int:
    """Report token health; exit 1 when any file is corrupt."""
    if auths_dir is None:
        auths_dir = get_auths_dir()
    files = sorted(auths_dir.glob("*.json")) if auths_dir.is_dir() else []
    bad = [f for f in files if not is_valid_json_file(f)]
    info(f"auths/: {len(files) - len(bad)} ok, {len(bad)} corrupted")
    for f in bad:
        info(f"  corrupted: {f.name}")
    return 1 if bad else 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    commands = {"backup": cmd_backup, "restore": cmd_restore, "verify": cmd_verify}
    if len(argv) != 1 or argv[0] not in commands:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        return commands[argv[0]]()
    except (OSError, RuntimeError) as e:
        warn(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
