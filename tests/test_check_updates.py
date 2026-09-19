import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from check_updates import (
    check_for_update,
    clean_version_str,
    get_local_version,
    is_newer,
    parse_semver,
    prompt_update_if_available,
)

def test_check_updates():
    # 1. SemVer comparison
    assert parse_semver("1.1.0") == (1, 1, 0)
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("0.153.0") == (0, 153, 0)
    assert is_newer("1.2.0", "1.1.0") is True
    assert is_newer("1.1.1", "1.1.0") is True
    assert is_newer("1.1.2", "1.1.1") is True
    assert is_newer("1.1.0", "1.1.0") is False
    assert is_newer("1.0.9", "1.1.0") is False
    assert is_newer("2.0.0", "1.99.99") is True

    # 2. String sanitization (BOM & prefix)
    assert clean_version_str("\ufeffv1.1.0\n") == "1.1.0"

    # 3. Local version reading (dynamically verified against the single source of truth)
    local_ver = get_local_version()
    expected_ver = (ROOT_DIR / "VERSION").read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    assert local_ver == expected_ver
    assert len(parse_semver(local_ver)) >= 3
    assert all(isinstance(part, int) and part >= 0 for part in parse_semver(local_ver))

    # 4. Direct remote check and explicit offline result (no persistent cache)
    with patch("check_updates.fetch_remote_version", return_value="1.2.0"):
        has_update, checked_local, remote = check_for_update()
        assert has_update is True
        assert checked_local == local_ver
        assert remote == "1.2.0"

    with patch("check_updates.fetch_remote_version", return_value=None):
        has_update, checked_local, remote = check_for_update()
        assert has_update is False
        assert checked_local == local_ver
        assert remote == "unknown"

    # 5. Non-interactive guard (Must not prompt or hang in automated envs)
    os.environ["AIC_NON_INTERACTIVE"] = "1"
    try:
        assert prompt_update_if_available() is False
    finally:
        os.environ.pop("AIC_NON_INTERACTIVE", None)

    return True, "Self-update checker, semver parser, direct remote check, and non-interactive guard passed."

if __name__ == "__main__":
    ok, msg = test_check_updates()
    print(f"PASS: {msg}" if ok else f"FAIL: {msg}")
