#!/usr/bin/env python3
# ==============================================================================
#  manage_profile.py - Atomic Shell Profile Block Manager
#  Python stdlib-only. Works across PowerShell, Bash, and Zsh profiles.
# ==============================================================================

import os
import sys
import json
import time
import base64
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from log_utils import error, info, warn  # noqa: E402

START_MARKER = b"# >>> AIC >>>"
END_MARKER = b"# <<< AIC <<<"


def detect_newline(content: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in content else b"\n"


def find_marker_span(content: bytes) -> tuple:
    """
    Returns (start_idx, end_idx) of the AIC block lines, or (-1, -1) if absent.
    Raises ValueError if markers are mismatched or corrupted.
    """
    start_count = content.count(START_MARKER)
    end_count = content.count(END_MARKER)

    if start_count != end_count:
        raise ValueError(
            f"Mismatched AIC markers detected (start: {start_count}, end: {end_count}). "
            "Aborting to avoid corrupting profile."
        )

    if start_count > 1:
        raise ValueError(f"Multiple AIC blocks ({start_count}) detected in profile. Aborting.")

    if start_count == 0:
        return (-1, -1)

    start_pos = content.find(START_MARKER)
    end_pos = content.find(END_MARKER)

    if start_pos > end_pos:
        raise ValueError("Corrupted AIC marker order: end marker appears before start marker.")

    # Expand start_pos backwards to line start
    line_start = content.rfind(b"\n", 0, start_pos)
    line_start = 0 if line_start == -1 else line_start + 1

    # Expand end_pos forwards to line end (including newline)
    line_end = content.find(b"\n", end_pos)
    line_end = len(content) if line_end == -1 else line_end + 1

    return (line_start, line_end)


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{time.time_ns()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def cmd_install(profile_path: Path, block_bytes: bytes, state_file: Path = None) -> int:
    file_existed = profile_path.exists()
    orig_content = profile_path.read_bytes() if file_existed else b""

    # 1. Validate markers BEFORE touching state or profile
    try:
        start_idx, end_idx = find_marker_span(orig_content)
    except ValueError as e:
        error(str(e))
        return 1

    # 2. Record transaction baseline in state_file if provided
    if state_file:
        orig_block = orig_content[start_idx:end_idx] if start_idx != -1 else b""
        state_data = {
            "file_existed": file_existed,
            "original_block_present": (start_idx != -1),
            "original_block_b64": base64.b64encode(orig_block).decode("ascii"),
            "timestamp": time.time(),
        }
        atomic_write_bytes(state_file, json.dumps(state_data).encode("utf-8"))

    # 3. Match newline style of existing profile
    nl = detect_newline(orig_content) if orig_content else (b"\r\n" if sys.platform == "win32" else b"\n")
    # Normalize block_bytes to match target newline style
    raw_lines = block_bytes.replace(b"\r\n", b"\n").split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines = raw_lines[:-1]
    norm_block = nl.join(raw_lines) + nl

    if start_idx != -1:
        new_content = orig_content[:start_idx] + norm_block + orig_content[end_idx:]
    else:
        if orig_content:
            if not orig_content.endswith(nl):
                orig_content += nl
            new_content = orig_content + norm_block
        else:
            new_content = norm_block

    atomic_write_bytes(profile_path, new_content)
    return 0


def cmd_uninstall(profile_path: Path) -> int:
    if not profile_path.exists():
        return 0

    content = profile_path.read_bytes()
    try:
        start_idx, end_idx = find_marker_span(content)
    except ValueError as e:
        error(str(e))
        return 1

    if start_idx != -1:
        remaining = content[:start_idx] + content[end_idx:]
        if not remaining.strip():
            try:
                profile_path.unlink()
                return 0
            except Exception as e:
                error(f"failed to remove empty profile {profile_path}: {e}")
                return 1
        else:
            # Preserve remaining content byte-exact without stripping trailing user newlines
            atomic_write_bytes(profile_path, remaining)
            return 0
    else:
        # Check legacy function/alias outside block
        text = content.decode("utf-8", "replace")
        import re
        cleaned = text
        if "function global:aic" in text:
            cleaned = re.sub(r'(?m)^\s*function\s+global:aic\s*\{.*aic\.py.*\}\r?\n?', '', cleaned)
        if "alias aic=" in text:
            cleaned = re.sub(r'(?m)^\s*alias\s+aic=.*aic\.py.*(\r?\n)?', '', cleaned)
        if cleaned != text:
            if not cleaned.strip():
                try:
                    profile_path.unlink()
                except Exception as e:
                    error(f"failed to remove empty profile {profile_path}: {e}")
                    return 1
            else:
                atomic_write_bytes(profile_path, cleaned.encode("utf-8"))

    return 0


def cmd_rollback(profile_path: Path, state_file: Path) -> int:
    if not state_file or not state_file.exists():
        # Fallback to uninstall cleanup
        return cmd_uninstall(profile_path)

    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        file_existed = state_data.get("file_existed", False)
        original_block_present = state_data.get("original_block_present", False)
        orig_block = base64.b64decode(state_data.get("original_block_b64", ""))

        if not profile_path.exists():
            state_file.unlink()
            return 0

        curr = profile_path.read_bytes()
        try:
            start_idx, end_idx = find_marker_span(curr)
        except ValueError as e:
            error(f"corrupted profile markers during rollback: {e}")
            return 1

        if original_block_present and orig_block:
            # Restore the previous AIC block
            if start_idx != -1:
                restored = curr[:start_idx] + orig_block + curr[end_idx:]
            else:
                nl = detect_newline(curr)
                restored = curr + nl + orig_block
            atomic_write_bytes(profile_path, restored)
        else:
            # Remove managed AIC block
            if start_idx != -1:
                remaining = curr[:start_idx] + curr[end_idx:]
            else:
                remaining = curr

            # If file did not exist prior to install AND no user content was added
            if not file_existed and not remaining.strip():
                try:
                    profile_path.unlink()
                except Exception as e:
                    error(f"failed to remove profile {profile_path}: {e}")
                    return 1
            else:
                # File existed before install OR user added content after install: preserve!
                atomic_write_bytes(profile_path, remaining)

        state_file.unlink()
        return 0
    except Exception as e:
        error(f"profile rollback failed: {e}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic shell profile block manager")
    parser.add_argument("--profile", required=True, help="Path to profile file")
    parser.add_argument("--action", required=True, choices=["install", "uninstall", "rollback"])
    parser.add_argument("--block-file", help="Path to file containing block content")
    parser.add_argument("--block-text", help="Literal text for block content")
    parser.add_argument("--state-file", help="Path to rollback state file")

    args = parser.parse_args()
    profile_path = Path(args.profile)
    state_file = Path(args.state_file) if args.state_file else None

    if args.action == "install":
        block_bytes = b""
        if args.block_file:
            block_bytes = Path(args.block_file).read_bytes()
        elif args.block_text:
            block_bytes = args.block_text.encode("utf-8")
        else:
            error("install requires either --block-file or --block-text")
            return 1
        return cmd_install(profile_path, block_bytes, state_file)

    elif args.action == "uninstall":
        return cmd_uninstall(profile_path)

    elif args.action == "rollback":
        return cmd_rollback(profile_path, state_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
