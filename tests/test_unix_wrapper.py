#!/usr/bin/env python3
"""
Comprehensive unit tests for bin/aic Unix wrapper:
1. Contract A:
   - Wrapper returns exit 42.
   - Contains 'Specific Error Output' in stderr.
   - Invocation count is exactly 1 (no loop/retry).
   - Arguments received match ['arg with spaces', '--flag'].
2. Contract B:
   - Fixture directory path contains spaces.
3. Contract C:
   - Custom isolated PATH containing Python but NO dirname.
   - Test actively constructs this PATH, independent of runner environment.
4. Contract D:
   - Only 'python' in PATH (no 'python3').
   - Fallback executes cleanly and exactly once.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def make_python_shim(target_dir: Path, shim_name: str) -> Path:
    py_posix = to_posix_path(Path(sys.executable))
    shim = target_dir / shim_name
    shim.write_text(f"""#!/bin/sh
exec "{py_posix}" "$@"
""", encoding="utf-8")
    try:
        shim.chmod(0o755)
    except Exception:
        pass
    return shim


def to_posix_path(p: Path) -> str:
    s = str(p.resolve())
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/")
        return f"/{drive}{rest}"
    return s.replace("\\", "/")


class TestUnixWrapper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aic_wrap_test_")
        self.bash_bin = "bash"
        if sys.platform == "win32":
            git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
            if git_bash.exists():
                self.bash_bin = str(git_bash).replace("\\", "/")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_fixture(self, base_dir: Path):
        bin_dir = base_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        counter_file = base_dir / "call_count.txt"
        counter_file.write_text("0", encoding="utf-8")

        argv_file = base_dir / "argv.json"

        counter_file_str = str(counter_file).replace("\\", "/")
        argv_file_str = str(argv_file).replace("\\", "/")

        fake_aic_py = bin_dir / "aic.py"
        fake_aic_py.write_text(f"""#!/usr/bin/env python3
import sys, json
from pathlib import Path
cnt_file = Path(r'{counter_file_str}')
argv_file = Path(r'{argv_file_str}')
count = int(cnt_file.read_text().strip()) + 1
cnt_file.write_text(str(count))
argv_file.write_text(json.dumps(sys.argv[1:]))
sys.stderr.write("Specific Error Output\\n")
sys.exit(42)
""", encoding="utf-8")

        # Copy real bin/aic wrapper
        shutil.copy2(ROOT_DIR / "bin" / "aic", bin_dir / "aic")
        return bin_dir / "aic", counter_file, argv_file

    def _assert_contract_a(self, res, counter_file, argv_file):
        # 1. Must preserve exit code 42
        self.assertEqual(res.returncode, 42, f"Expected 42, got {res.returncode}. Stderr: {res.stderr}")
        # 2. Must preserve stderr
        self.assertIn("Specific Error Output", res.stderr)
        # 3. Must have called aic.py exactly once
        self.assertEqual(counter_file.read_text().strip(), "1")
        # 4. Must preserve arguments with spaces
        self.assertTrue(argv_file.exists())
        self.assertEqual(json.loads(argv_file.read_text()), ["arg with spaces", "--flag"])

    # Test A: Basic fixture contract
    def test_contract_a_basic(self):
        aic_path, counter_file, argv_file = self._setup_fixture(Path(self.temp_dir))
        script_path = str(aic_path).replace("\\", "/")
        res = subprocess.run([self.bash_bin, script_path, "arg with spaces", "--flag"], capture_output=True, text=True)
        self._assert_contract_a(res, counter_file, argv_file)

    # Test B: Fixture path contains spaces
    def test_contract_b_fixture_path_with_spaces(self):
        space_dir = Path(tempfile.mkdtemp(prefix="aic wrap test with spaces "))
        try:
            aic_path, counter_file, argv_file = self._setup_fixture(space_dir)
            script_path = str(aic_path).replace("\\", "/")
            res = subprocess.run([self.bash_bin, script_path, "arg with spaces", "--flag"], capture_output=True, text=True)
            self._assert_contract_a(res, counter_file, argv_file)
        finally:
            shutil.rmtree(space_dir, ignore_errors=True)

    # Test C: Isolated PATH with Python but NO dirname
    def test_contract_c_path_without_dirname(self):
        shim_dir = Path(tempfile.mkdtemp(prefix="aic_nodirname_"))
        try:
            make_python_shim(shim_dir, "python3")
            make_python_shim(shim_dir, "python")

            bash_dir = to_posix_path(Path(self.bash_bin).parent)
            shim_path_str = f"{to_posix_path(shim_dir)}:{bash_dir}"

            # Verify dirname is strictly absent when PATH is set to custom PATH
            chk_dirname = subprocess.run([self.bash_bin, "-c", f'PATH="{shim_path_str}"; command -v dirname'], capture_output=True)
            self.assertNotEqual(chk_dirname.returncode, 0, "Test prerequisite failed: dirname must not exist in custom PATH")

            # Verify python3 is found
            chk_py = subprocess.run([self.bash_bin, "-c", f'PATH="{shim_path_str}"; command -v python3'], capture_output=True)
            self.assertEqual(chk_py.returncode, 0, "Test prerequisite failed: python3 must exist in custom PATH")

            aic_path, counter_file, argv_file = self._setup_fixture(Path(self.temp_dir))
            script_path = str(aic_path).replace("\\", "/")

            res = subprocess.run(
                [self.bash_bin, "-c", f'PATH="{shim_path_str}"; bash "{script_path}" "$@"', "--", "arg with spaces", "--flag"],
                capture_output=True, text=True
            )
            self._assert_contract_a(res, counter_file, argv_file)
        finally:
            shutil.rmtree(shim_dir, ignore_errors=True)

    # Test D: Only 'python' in PATH (no 'python3')
    def test_contract_d_only_python_fallback(self):
        shim_dir = Path(tempfile.mkdtemp(prefix="aic_onlypy_"))
        try:
            make_python_shim(shim_dir, "python")

            bash_dir = to_posix_path(Path(self.bash_bin).parent)
            shim_path_str = f"{to_posix_path(shim_dir)}:{bash_dir}"

            # Verify python3 is strictly absent
            chk_py3 = subprocess.run([self.bash_bin, "-c", f'PATH="{shim_path_str}"; command -v python3'], capture_output=True)
            self.assertNotEqual(chk_py3.returncode, 0, "python3 must not exist")

            # Verify python is found
            chk_py = subprocess.run([self.bash_bin, "-c", f'PATH="{shim_path_str}"; command -v python'], capture_output=True)
            self.assertEqual(chk_py.returncode, 0, "python must exist")

            aic_path, counter_file, argv_file = self._setup_fixture(Path(self.temp_dir))
            script_path = str(aic_path).replace("\\", "/")
            res = subprocess.run(
                [self.bash_bin, "-c", f'PATH="{shim_path_str}"; bash "{script_path}" "$@"', "--", "arg with spaces", "--flag"],
                capture_output=True, text=True
            )
            self._assert_contract_a(res, counter_file, argv_file)
        finally:
            shutil.rmtree(shim_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
