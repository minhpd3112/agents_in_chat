import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from check_updates import parse_semver, is_newer, clean_version_str, get_local_version, read_cache, write_cache, prompt_update_if_available

def test_check_updates():
    # 1. SemVer comparison
    assert parse_semver("1.1.0") == (1, 1, 0)
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("0.153.0") == (0, 153, 0)
    assert is_newer("1.2.0", "1.1.0") is True
    assert is_newer("1.1.1", "1.1.0") is True
    assert is_newer("1.1.0", "1.1.0") is False
    assert is_newer("1.0.9", "1.1.0") is False
    assert is_newer("2.0.0", "1.99.99") is True

    # 2. String sanitization (BOM & prefix)
    assert clean_version_str("\ufeffv1.1.0\n") == "1.1.0"

    # 3. Local version reading
    local_ver = get_local_version()
    assert local_ver == "1.1.1"

    # 4. Cache read/write in isolated directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["AIC_CODEX_DIR"] = tmp_dir
        try:
            assert read_cache() == {}
            write_cache({"latest_version": "1.2.0", "last_checked_at": 123456.0})
            cdata = read_cache()
            assert cdata.get("latest_version") == "1.2.0"
            assert cdata.get("last_checked_at") == 123456.0
        finally:
            os.environ.pop("AIC_CODEX_DIR", None)

    # 5. Non-interactive guard (Must not prompt or hang in automated envs)
    os.environ["AIC_NON_INTERACTIVE"] = "1"
    try:
        assert prompt_update_if_available() is False
    finally:
        os.environ.pop("AIC_NON_INTERACTIVE", None)

    return True, "Self-update checker, semver parser, cache serialization, and non-interactive guard passed."

if __name__ == "__main__":
    ok, msg = test_check_updates()
    print(f"PASS: {msg}" if ok else f"FAIL: {msg}")
