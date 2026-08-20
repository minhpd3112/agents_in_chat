import os
import sys
from pathlib import Path

# Add scripts directory to path to import sync_sessions
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from sync_sessions import verify_provider, get_codex_dir

def test_sqlite_and_sessions(codex_dir: Path = None):
    target_dir = codex_dir or get_codex_dir()
    code = verify_provider("custom", target_dir)
    if code == 0:
        return True, "SQLite threads and session headers all verified matching provider='custom'."
    else:
        return False, "Verification failed for SQLite threads or session headers."

if __name__ == "__main__":
    ok, msg = test_sqlite_and_sessions()
    print(f"[{'PASS' if ok else 'FAIL'}] SQLite & Sessions: {msg}")
    sys.exit(0 if ok else 1)
