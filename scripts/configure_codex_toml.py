#!/usr/bin/env python3
import os
import sys
import re
import json
import hashlib
import time
import argparse
import shutil
from pathlib import Path

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
        print(f"[ERROR] Manifest bi hong hoac khong dung JSON: {e}")
        return False, {}

    if not isinstance(manifest_data, dict):
        print(f"[ERROR] Manifest root must be a JSON object!")
        return False, {}

    orig_exists = manifest_data.get("original_exists")
    if not isinstance(orig_exists, bool):
        print(f"[ERROR] Manifest field 'original_exists' must be boolean!")
        return False, {}

    if orig_exists:
        if not backup_file.exists():
            print(f"[ERROR] Manifest ghi nhan config ton tai nhung khong tim thay file {backup_file}!")
            return False, {}
        expected_sha = manifest_data.get("sha256")
        if not expected_sha:
            print(f"[ERROR] Manifest missing 'sha256' checksum field!")
            return False, {}
        actual_sha = compute_sha256_file(backup_file)
        if actual_sha != expected_sha:
            print(f"[ERROR] Backup checksum mismatch! Expected: {expected_sha}, Actual: {actual_sha}")
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
            print("[ERROR] Backup manifest hien co bi hong! Aborting install.")
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
        print(f"[OK] Da tao backup ban dau tai {backup_file} (SHA256: {actual_sha[:8]}...)")
    else:
        manifest_data = {
            "original_exists": False,
            "sha256": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
        atomic_write_bytes(manifest_path, manifest_bytes)
        print("[OK] Ghi nhan trang thai ban dau khong co config.toml.")

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
    print("Cleaned and configured config.toml for provider: 'custom'!")
    return 0


def restore_original(codex_dir: Path) -> int:
    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == "restore-config":
        print("[FAIL_INJECTION] Injected failure at restore-config")
        return 1

    backup_dir = codex_dir / "aic-backup"
    manifest_path = backup_dir / "manifest.json"
    backup_file = backup_dir / "config.toml.bak"
    config_path = codex_dir / "config.toml"

    # Mode 1: Restore from official backup manifest
    if manifest_path.exists():
        valid, manifest_data = validate_existing_manifest(manifest_path, backup_file)
        if not valid:
            print("[ERROR] Khong the khoi phuc vi backup manifest bi loi!")
            return 1

        if manifest_data.get("original_exists"):
            raw_backup_bytes = backup_file.read_bytes()
            atomic_write_bytes(config_path, raw_backup_bytes)
            print("[OK] Da khoi phuc chinh xac config.toml goc tu backup ban dau (byte-exact).")
            return 0
        else:
            if config_path.exists():
                config_path.unlink()
            print("[OK] Da go bo config.toml vi ban dau nguoi dung chua tung tao file nay.")
            return 0

    # Mode 2: Legacy Fallback (No backup manifest found)
    print("[WARN] Khong tim thay backup manifest, tien hanh don dep an toan (Legacy Fallback)...")
    if not config_path.exists():
        print("[OK] Khong co config.toml de xu ly.")
        return 0

    try:
        raw_bytes = config_path.read_bytes()
        recovery_path = codex_dir / f"config.toml.recovery.{int(time.time())}"
        atomic_write_bytes(recovery_path, raw_bytes)
        print(f"[INFO] Da tao recovery copy tai {recovery_path}")

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
        print("[OK] Da lam sach cac muc cua AIC trong top-level va bao toan 100% cac section nguoi dung.")
        return 0
    except Exception as e:
        print(f"[ERROR] Legacy fallback gap loi: {e}")
        return 1


def clean_backup_dir(codex_dir: Path) -> int:
    backup_dir = codex_dir / "aic-backup"
    if backup_dir.exists():
        try:
            shutil.rmtree(backup_dir)
            print("[OK] Da don dep thu muc aic-backup sau khi uninstall thanh cong.")
        except Exception as e:
            print(f"[WARN] Khong the xoa aic-backup: {e}")
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
        print(f"[ERROR] Unknown action: {action}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
