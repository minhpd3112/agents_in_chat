#!/usr/bin/env python3
import sys
import os
import sqlite3
import json
import argparse
import time
from pathlib import Path

VALID_PROVIDERS = {"custom", "openai"}

def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def sync_provider(target_provider: str, codex_dir: Path) -> int:
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        print(f"[ERROR] Provider khong hop le: '{target_provider}'. Chi chap nhan 'custom' hoac 'openai'.")
        return 2

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == f"sync-{target_provider}":
        print(f"[FAIL_INJECTION] Injected failure at sync-{target_provider}")
        return 1

    db_path = codex_dir / "state_5.sqlite"
    sessions_dir = codex_dir / "sessions"

    had_errors = False

    # 1. Sync SQLite threads table safely
    updated_threads = 0
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            with conn:
                c = conn.cursor()
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threads';")
                if c.fetchone():
                    c.execute("UPDATE threads SET model_provider = ? WHERE model_provider IS NOT NULL;", (target_provider,))
                    updated_threads = c.rowcount
        except Exception as e:
            print(f"[ERROR] Failed to update state_5.sqlite: {e}")
            had_errors = True
        finally:
            if conn:
                conn.close()

    # 2. Sync JSONL session headers with atomic file replace
    updated_files = 0
    failed_files = []
    if sessions_dir.exists():
        for root, _, files in os.walk(str(sessions_dir)):
            for f in files:
                if f.endswith(".jsonl"):
                    file_path = os.path.join(root, f)
                    tmp_file_path = f"{file_path}.tmp.{os.getpid()}_{time.time_ns()}"
                    try:
                        with open(file_path, "r", encoding="utf-8") as sfile:
                            lines = sfile.readlines()
                        if not lines:
                            failed_files.append((file_path, "Empty JSONL file"))
                            continue

                        try:
                            meta = json.loads(lines[0])
                        except Exception as parse_err:
                            failed_files.append((file_path, f"Malformed JSON in line 1: {parse_err}"))
                            continue

                        if not isinstance(meta, dict) or "payload" not in meta or not isinstance(meta["payload"], dict):
                            failed_files.append((file_path, "Invalid session schema (missing dict payload)"))
                            continue

                        needs_write = False
                        if meta["payload"].get("model_provider") != target_provider:
                            meta["payload"]["model_provider"] = target_provider
                            lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"
                            needs_write = True

                        # When restoring to OpenAI, sanitize foreign/synthetic reasoning tokens (cpa-) that OpenAI cannot decrypt
                        if target_provider == "openai":
                            cleaned_lines = [lines[0]]
                            carrier_dropped = 0
                            for l_idx in range(1, len(lines)):
                                l_str = lines[l_idx].strip()
                                if not l_str:
                                    continue
                                try:
                                    item_data = json.loads(l_str)
                                    if item_data.get("type") == "response_item":
                                        p = item_data.get("payload")
                                        if isinstance(p, dict) and p.get("type") == "reasoning":
                                            enc = p.get("encrypted_content")
                                            if enc and isinstance(enc, str) and ("cpa-" in enc or enc.startswith("cpa-")):
                                                carrier_dropped += 1
                                                needs_write = True
                                                continue
                                    cleaned_lines.append(lines[l_idx])
                                except Exception:
                                    cleaned_lines.append(lines[l_idx])
                            if carrier_dropped > 0:
                                lines = cleaned_lines

                        if needs_write:
                            try:
                                with open(tmp_file_path, "w", encoding="utf-8") as tmp_file:
                                    tmp_file.writelines(lines)
                                    tmp_file.flush()
                                    os.fsync(tmp_file.fileno())
                                os.replace(tmp_file_path, file_path)
                                updated_files += 1
                            except PermissionError:
                                # On Windows, active Codex process opens files with share read/write but not delete
                                with open(file_path, "r+", encoding="utf-8") as target_file:
                                    target_file.seek(0)
                                    target_file.writelines(lines)
                                    target_file.truncate()
                                    target_file.flush()
                                    os.fsync(target_file.fileno())
                                updated_files += 1
                    except Exception as e:
                        failed_files.append((file_path, str(e)))
                    finally:
                        if os.path.exists(tmp_file_path):
                            try:
                                os.remove(tmp_file_path)
                            except Exception:
                                pass

    if failed_files:
        had_errors = True
        print(f"[ERROR] Co {len(failed_files)} file session gap loi khi dong bo:")
        for fp, err in failed_files:
            print(f"  - {fp}: {err}")

    # 3. Invalidate/clear stale thread_history projection cache if files/threads were updated
    history_db = codex_dir / "thread_history_1.sqlite"
    if history_db.exists() and (updated_files > 0 or updated_threads > 0):
        h_conn = None
        try:
            h_conn = sqlite3.connect(str(history_db))
            with h_conn:
                h_c = h_conn.cursor()
                h_c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='thread_history_projection_state';")
                if h_c.fetchone():
                    h_c.execute("DELETE FROM thread_items;")
                    h_c.execute("DELETE FROM thread_turns;")
                    h_c.execute("DELETE FROM thread_history_projection_state;")
        except Exception:
            pass
        finally:
            if h_conn:
                h_conn.close()

    print(f"Synced {updated_threads} SQLite threads and {updated_files} session files to provider '{target_provider}'.")
    return 1 if had_errors else 0


def verify_provider(target_provider: str, codex_dir: Path) -> int:
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        print(f"[ERROR] Provider khong hop le: '{target_provider}'. Chi chap nhan 'custom' hoac 'openai'.")
        return 2

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == f"verify-{target_provider}":
        print(f"[FAIL_INJECTION] Injected failure at verify-{target_provider}")
        return 1

    db_path = codex_dir / "state_5.sqlite"
    sessions_dir = codex_dir / "sessions"

    errors = []
    checked_threads = 0
    checked_files = 0

    # 1. Verify SQLite threads table
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threads';")
            if c.fetchone():
                c.execute("SELECT COUNT(*) FROM threads WHERE model_provider != ? AND model_provider IS NOT NULL;", (target_provider,))
                mismatched = c.fetchone()[0]
                if mismatched > 0:
                    errors.append(f"state_5.sqlite contains {mismatched} threads with model_provider != '{target_provider}'")
                c.execute("SELECT COUNT(*) FROM threads WHERE model_provider IS NOT NULL;")
                checked_threads = c.fetchone()[0]
        except Exception as e:
            errors.append(f"Failed to query state_5.sqlite: {e}")
        finally:
            if conn:
                conn.close()

    # 2. Verify JSONL files
    if sessions_dir.exists():
        for root, _, files in os.walk(str(sessions_dir)):
            for f in files:
                if f.endswith(".jsonl"):
                    checked_files += 1
                    file_path = os.path.join(root, f)
                    try:
                        with open(file_path, "r", encoding="utf-8") as sfile:
                            first_line = sfile.readline()
                        if not first_line:
                            errors.append(f"Empty session file: {file_path}")
                            continue
                        meta = json.loads(first_line)
                        if not isinstance(meta, dict) or "payload" not in meta or not isinstance(meta["payload"], dict):
                            errors.append(f"Invalid schema in session file: {file_path}")
                            continue
                        current_prov = meta["payload"].get("model_provider")
                        if current_prov != target_provider:
                            errors.append(f"Session {f} has model_provider='{current_prov}' (Expected '{target_provider}')")
                    except Exception as e:
                        errors.append(f"Failed to verify {file_path}: {e}")

    if errors:
        print(f"[FAIL] Verification failed with {len(errors)} issues:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"[PASS] Verified {checked_threads} SQLite threads and {checked_files} session files match provider '{target_provider}'.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agents in Chat - Codex Session History Sync & Verify Helper")
    parser.add_argument("provider", nargs="?", help="Target model provider: 'custom' or 'openai'")
    parser.add_argument("--verify", action="store_true", help="Run in verification mode without modifying files")
    parser.add_argument("--codex-dir", default=None, help="Custom path to .codex directory (for tests / isolation)")

    args, remaining = parser.parse_known_args()

    # Support positional 'verify' keyword as alternative syntax: sync_sessions.py verify <provider>
    is_verify = args.verify
    provider = args.provider
    if provider and provider.lower() == "verify" and remaining:
        is_verify = True
        provider = remaining[0]
    elif not provider and remaining:
        provider = remaining[0]

    if not provider:
        print("Usage: python sync_sessions.py [--verify] <custom|openai> [--codex-dir <path>]")
        return 2

    codex_dir = get_codex_dir(args.codex_dir)

    if is_verify:
        return verify_provider(provider, codex_dir)
    else:
        return sync_provider(provider, codex_dir)


if __name__ == "__main__":
    sys.exit(main())
