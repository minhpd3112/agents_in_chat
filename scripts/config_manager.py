#!/usr/bin/env python3
"""Config manager for Codex config.toml: install, restore, and byte-exact backups.

Status messages are sent to stderr to keep stdout clean for automation.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from log_utils import error, info, warn

# Exact legacy one-line instructions injected by AIC v1.1.5 that must be stripped on update
LEGACY_AIC_INSTRUCTIONS = [
    '"You are Codex, an expert coding agent."',
    "'You are Codex, an expert coding agent.'",
    "You are Codex, an expert coding agent.",
]


def is_legacy_aic_instruction(line: str) -> bool:
    """Return True if the line specifies the top-level 'instructions' key with
    the exact legacy AIC-owned one-liner.
    Strictly checks the exact key 'instructions' (never matches model_instructions_file, etc.)
    and preserves any user-defined custom instructions."""
    m = re.match(r"^[ \t]*instructions[ \t]*=[ \t]*(.*)$", line.strip())
    if not m:
        return False
    val = m.group(1).strip()
    return val in LEGACY_AIC_INSTRUCTIONS


def atomic_write_bytes(target_path: Path, data: bytes) -> None:
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


def validate_existing_manifest(manifest_path: Path, backup_file: Path) -> Tuple[bool, dict]:
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
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
        atomic_write_bytes(manifest_path, manifest_bytes)
        info(f"saved initial config.toml backup (sha256 {actual_sha[:8]})")
    else:
        manifest_data = {
            "original_exists": False,
            "sha256": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
        atomic_write_bytes(manifest_path, manifest_bytes)
        info("no pre-existing config.toml; recorded clean-install state")

    return True


def split_toml_sections(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    section_pattern = re.compile(r"^[ \t]*\[([^\]]+)\]", re.MULTILINE)
    matches = list(section_pattern.finditer(text))

    if not matches:
        return text.strip(), []

    top_level = text[: matches[0].start()].strip()
    sections = []
    for i, m in enumerate(matches):
        sec_header = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sec_content = text[start:end].strip()
        sections.append((sec_header, sec_content))

    return top_level, sections


def get_proxy_port() -> int:
    env_port = os.environ.get("AIC_PORT")
    if env_port:
        try:
            return int(env_port)
        except (ValueError, TypeError):
            pass
    config_yaml = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_yaml.exists():
        try:
            m = re.search(r"^port:\s*(\d+)", config_yaml.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                return int(m.group(1))
        except Exception:
            pass
    return 8090


def configure_custom(codex_dir: Path) -> int:
    """Configure ~/.codex/config.toml to route through AIC local reverse proxy.

    Guarantees:
    - Never injects a top-level single-sentence 'instructions' line, so Codex CLI
      uses the full instruction templates from models_cache.json.
    - Cleans any legacy AIC-injected single-sentence instructions from v1.1.5.
    - Strictly preserves user-defined custom instructions and non-AIC top-level keys.
    """
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

    filtered_top_lines = []
    if top_level:
        for line in top_level.splitlines():
            s = line.strip()
            # Strip AIC-managed routing keys using exact key matching
            if (
                re.match(r"^[ \t]*model[ \t]*=", s)
                or re.match(r"^[ \t]*model_provider[ \t]*=", s)
                or re.match(r"^[ \t]*model_reasoning_effort[ \t]*=", s)
                or re.match(r"^[ \t]*service_tier[ \t]*=", s)
            ):
                continue
            # Strip legacy AIC one-line instructions, preserve user custom instructions
            if is_legacy_aic_instruction(s):
                continue
            filtered_top_lines.append(line)

    lines = [
        'model = "gemini-3.8-flash"',
        'model_reasoning_effort = "high"',
        'service_tier = "default"',
        'model_provider = "custom"',
    ]
    # Note: NO top-level 'instructions = ...' is added!

    for l in filtered_top_lines:
        if l.strip():
            lines.append(l)

    port = get_proxy_port()
    custom_provider_block = (
        '[model_providers.custom]\n'
        'name = "Custom Quota Pool"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n'
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
    """Restore original config.toml byte-for-byte from backup manifest,
    or use non-destructive legacy fallback."""
    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "restore-config":
        info("injected failure at restore-config")
        return 1

    backup_dir = codex_dir / "aic-backup"
    manifest_path = backup_dir / "manifest.json"
    backup_file = backup_dir / "config.toml.bak"
    config_path = codex_dir / "config.toml"

    if manifest_path.exists():
        valid, manifest_data = validate_existing_manifest(manifest_path, backup_file)
        if not valid:
            error("cannot restore: backup manifest invalid")
            return 1

        if manifest_data.get("original_exists"):
            raw_backup = backup_file.read_bytes()
            atomic_write_bytes(config_path, raw_backup)
            info("restored original config.toml from backup (byte-exact)")
            return 0
        else:
            if config_path.exists():
                try:
                    config_path.unlink()
                    info("removed config.toml (none existed before install)")
                    return 0
                except Exception as e:
                    error(f"failed to remove config.toml: {e}")
                    return 1
            return 0

    warn("no backup manifest found; using legacy fallback cleanup")
    return clean_aic_keys_legacy(codex_dir)


def clean_aic_keys_legacy(codex_dir: Path) -> int:
    config_path = codex_dir / "config.toml"
    if not config_path.exists():
        info("no config.toml found to clean")
        return 0

    try:
        raw_bytes = config_path.read_bytes()
        recovery_path = codex_dir / f"config.toml.recovery.{int(time.time())}"
        atomic_write_bytes(recovery_path, raw_bytes)
        info(f"recovery copy saved: {recovery_path.name}")

        try:
            content = raw_bytes.decode("utf-8-sig")
        except Exception:
            content = raw_bytes.decode("latin-1")

        top_level, sections = split_toml_sections(content)

        cleaned_top_lines = []
        for line in top_level.splitlines():
            s = line.strip()
            if re.match(r"^[ \t]*model_provider[ \t]*=", s) and '"custom"' in s:
                cleaned_top_lines.append('model_provider = "openai"')
            elif re.match(r"^[ \t]*model[ \t]*=", s) and any(m in s for m in ['"gemini-', '"claude-', '"muse-']):
                cleaned_top_lines.append('model = "gpt-5.6-sol"')
            elif is_legacy_aic_instruction(s):
                continue
            else:
                cleaned_top_lines.append(line)

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
