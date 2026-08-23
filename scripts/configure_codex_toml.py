#!/usr/bin/env python3
"""Codex config.toml manager: install/restore/backup with byte-exact safety.

Status goes to stderr so stdout stays clean for scripting.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from log_utils import error, info, warn

def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def atomic_write_bytes(target_path: Path, data: bytes):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f"{target_path.name}.tmp.{os.getpid()}_{time.time_ns()}"
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def validate_existing_manifest(manifest_path: Path, backup_file: Path) -> tuple[bool, dict]:
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest_data = json.loads(raw_manifest.decode("utf-8"))
    except Exception as e:
        error(f"corrupt backup manifest: {e}")
        return False, {}

    if not isinstance(manifest_data, dict):
        error("manifest root must be a JSON object")
        return False, {}

    orig_exists = manifest_data.get("original_exists")
    if not isinstance(orig_exists, bool):
        error("manifest field 'original_exists' must be boolean")
        return False, {}

    if orig_exists:
        if not backup_file.exists():
            error(f"manifest says config existed but backup file is missing: {backup_file}")
            return False, {}
        expected_sha = manifest_data.get("sha256")
        if not expected_sha:
            error("manifest missing 'sha256' checksum field")
            return False, {}
        actual_sha = compute_sha256_file(backup_file)
        if actual_sha != expected_sha:
            error(f"backup checksum mismatch (expected {expected_sha[:8]}..., got {actual_sha[:8]}...)")
            return False, {}

    return True, manifest_data


def ensure_backup(codex_dir: Path) -> bool:
    backup_dir = codex_dir / "aic-backup"
    manifest_path = backup_dir / "manifest.json"
    backup_file = backup_dir / "config.toml.bak"
    config_path = codex_dir / "config.toml"

    # If backup manifest already exists, validate it thoroughly
    if manifest_path.exists():
        valid, _ = validate_existing_manifest(manifest_path, backup_file)
        if not valid:
            error("backup manifest is corrupt; aborting install")
            return False
        return True

    backup_dir.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        raw_bytes = config_path.read_bytes()
        atomic_write_bytes(backup_file, raw_bytes)
        actual_sha = compute_sha256_bytes(raw_bytes)

        manifest_data = {
            "original_exists": True,
            "sha256": actual_sha,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
        atomic_write_bytes(manifest_path, manifest_bytes)
        info(f"saved initial config.toml backup (sha256 {actual_sha[:8]})")
    else:
        manifest_data = {
            "original_exists": False,
            "sha256": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
        atomic_write_bytes(manifest_path, manifest_bytes)
        info("no pre-existing config.toml; recorded clean-install state")

    return True


def split_toml_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    # Match lines like [section_name] or [section."key"]
    section_pattern = re.compile(r'^[ 	]*\[([^\]]+)\]', re.MULTILINE)
    matches = list(section_pattern.finditer(text))

    if not matches:
        return text.strip(), []

    top_level = text[:matches[0].start()].strip()
    sections = []
    for i, m in enumerate(matches):
        sec_header = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sec_content = text[start:end].strip()
        sections.append((sec_header, sec_content))

    return top_level, sections


def configure_custom(codex_dir: Path) -> int:
    # 1. Ensure initial backup exists before modifying; abort if backup is invalid
    if not ensure_backup(codex_dir):
        return 1

    config_path = codex_dir / "config.toml"
    existing_text = ""
    if config_path.exists():
        try:
            existing_text = config_path.read_bytes().decode("utf-8-sig")
        except Exception:
            existing_text = config_path.read_bytes().decode("latin-1")

    top_level, sections = split_toml_sections(existing_text)

    # Filter out AIC-managed keys from top level while preserving other top-level keys
    filtered_top_lines = []
    if top_level:
        for line in top_level.splitlines():
            s = line.strip()
            if s.startswith("model ") or s.startswith("model=") or s.startswith("model_provider") or s.startswith("model_reasoning_effort") or s.startswith("service_tier"):
                continue
            filtered_top_lines.append(line)

    lines = []
    lines.append('model = "gemini-3.7-flash"')
    lines.append('model_reasoning_effort = "high"')
    lines.append('service_tier = "default"')
    lines.append('model_provider = "custom"')
    for l in filtered_top_lines:
        if l.strip():
            lines.append(l)

    # Build custom provider block
    custom_provider_block = (
        '[model_providers.custom]\n'
        'name = "Custom Quota Pool"\n'
        'base_url = "http://127.0.0.1:8080/v1"\n'
        'wire_api = "responses"'
    )

    output_sections = [custom_provider_block]

    has_windows_sec = False
    for sec_header, sec_content in sections:
        if sec_header == "model_providers.custom":
            continue
        if sec_header in ["windows", "sandbox"]:
            has_windows_sec = True
        output_sections.append(sec_content)

    if not has_windows_sec and sys.platform == "win32":
        output_sections.append('[windows]\nsandbox = "elevated"')

    final_text = "\n".join(lines).strip() + "\n\n" + "\n\n".join(output_sections).strip() + "\n"
    atomic_write_bytes(config_path, final_text.encode("utf-8"))
    info("config.toml configured for provider 'custom'")
    return 0


def restore_original(codex_dir: Path) -> int:
    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "restore-config":
        info("injected failure at restore-config")
        return 1

    backup_dir = codex_dir / "aic-backup"
    manifest_path = backup_dir / "manifest.json"
    backup_file = backup_dir / "config.toml.bak"
    config_path = codex_dir / "config.toml"

    # Mode 1: Restore from official backup manifest
    if manifest_path.exists():
        valid, manifest_data = validate_existing_manifest(manifest_path, backup_file)
        if not valid:
            error("cannot restore: backup manifest invalid")
            return 1

        if manifest_data.get("original_exists"):
            raw_backup_bytes = backup_file.read_bytes()
            atomic_write_bytes(config_path, raw_backup_bytes)
            info("restored original config.toml from backup (byte-exact)")
            return 0
        else:
            if config_path.exists():
                config_path.unlink()
            info("removed config.toml (none existed before install)")
            return 0

    # Mode 2: Legacy Fallback (No backup manifest found)
    warn("no backup manifest found; using legacy fallback cleanup")
    if not config_path.exists():
        info("no config.toml to process")
        return 0

    try:
        raw_bytes = config_path.read_bytes()
        recovery_path = codex_dir / f"config.toml.recovery.{int(time.time())}"
        atomic_write_bytes(recovery_path, raw_bytes)
        info(f"recovery copy saved: {recovery_path.name}")

        try:
            text = raw_bytes.decode("utf-8-sig")
        except Exception:
            text = raw_bytes.decode("latin-1")

        top_level, sections = split_toml_sections(text)

        # In top level ONLY, replace AIC model and model_provider
        cleaned_top_lines = []
        for line in top_level.splitlines():
            s = line.strip()
            if s.startswith("model_provider") and '"custom"' in s:
                cleaned_top_lines.append('model_provider = "openai"')
            elif s.startswith("model") and '"gemini-3.7-flash"' in s and not s.startswith("model_"):
                cleaned_top_lines.append('model = "gpt-5.6-sol"')
            else:
                cleaned_top_lines.append(line)

        # In sections, remove ONLY [model_providers.custom], preserve ALL other sections 100%
        preserved_sections = []
        for sec_header, sec_content in sections:
            if sec_header == "model_providers.custom":
                continue
            preserved_sections.append(sec_content)

        final_text = "\n".join(cleaned_top_lines).strip()
        if preserved_sections:
            final_text += "\n\n" + "\n\n".join(preserved_sections).strip()
        final_text += "\n"

        atomic_write_bytes(config_path, final_text.encode("utf-8"))
        info("cleaned AIC keys from top-level; preserved all user sections")
        return 0
    except Exception as e:
        error(f"legacy fallback failed: {e}")
        return 1


def clean_backup_dir(codex_dir: Path) -> int:
    backup_dir = codex_dir / "aic-backup"
    if backup_dir.exists():
        try:
            shutil.rmtree(backup_dir)
            info("cleaned aic-backup directory")
        except Exception as e:
            warn(f"could not remove aic-backup: {e}")
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agents in Chat - Codex Config TOML Manager")
    parser.add_argument("action", nargs="?", default="custom", help="Action: 'custom', 'openai', 'restore', 'backup', 'clean-backup'")
    parser.add_argument("--codex-dir", default=None, help="Custom path to .codex directory")

    args = parser.parse_args()
    codex_dir = get_codex_dir(args.codex_dir)
    action = args.action.lower()

    if action in ["custom", "install", "aic"]:
        return configure_custom(codex_dir)
    elif action in ["openai", "restore", "uninstall"]:
        return restore_original(codex_dir)
    elif action in ["backup"]:
        ok = ensure_backup(codex_dir)
        return 0 if ok else 1
    elif action in ["clean-backup", "clean_backup"]:
        return clean_backup_dir(codex_dir)
    else:
        error(f"unknown action: {action}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
