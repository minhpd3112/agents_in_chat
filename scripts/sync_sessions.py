#!/usr/bin/env python3
"""Codex session history provider sync & verify helper.

Usage:
    python sync_sessions.py [--verify] [--check-instructions] <custom|openai> [--codex-dir <path>]

Status goes to stderr so stdout stays clean for scripting.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import List

from instruction_compat import (
    EXACT_FORBIDDEN_FINGERPRINTS,
    has_unsanitized_fingerprint,
    sanitize_instruction_text,
    sanitize_model_switch,
    sanitize_session_item,
)
from log_utils import error, info
from session_store import (
    atomic_write_lines,
    build_thread_map,
    find_ordinal_byte_offset,
    realign_forked_lineages as store_realign_lineages,
    verify_lineage_integrity,
)

VALID_PROVIDERS = {"custom", "openai"}


def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def realign_forked_lineages(sessions_dir: Path) -> int:
    """Wrapper around session_store.realign_forked_lineages.
    Returns count of realigned sessions. If any realignment error occurred,
    raises RuntimeError to guarantee failures are not silently swallowed."""
    realigned_count, error_count = store_realign_lineages(sessions_dir)
    if error_count > 0:
        raise RuntimeError(f"{error_count} lineage realignment error(s) occurred")
    return realigned_count


def clear_history_projection_cache(codex_dir: Path) -> bool:
    """Invalidate stale thread_history projection cache in SQLite with lock retries."""
    history_db = codex_dir / "thread_history_1.sqlite"
    if not history_db.exists():
        return True

    for attempt in range(3):
        h_conn = None
        try:
            h_conn = sqlite3.connect(str(history_db), timeout=5.0)
            with h_conn:
                h_c = h_conn.cursor()
                h_c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='thread_history_projection_state';")
                if h_c.fetchone():
                    h_c.execute("DELETE FROM thread_items;")
                    h_c.execute("DELETE FROM thread_turns;")
                    h_c.execute("DELETE FROM thread_history_projection_state;")
            return True
        except sqlite3.OperationalError:
            time.sleep(0.3)
        except Exception as e:
            info(f"warning: history projection cache clear: {e}")
            return False
        finally:
            if h_conn:
                h_conn.close()
    return False


def sync_provider(target_provider: str, codex_dir: Path) -> int:
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        error(f"invalid provider '{target_provider}' (expected custom|openai)")
        return 2

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == f"sync-{target_provider}":
        info(f"injected failure at sync-{target_provider}")
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
            error(f"failed to update state_5.sqlite: {e}")
            had_errors = True
        finally:
            if conn:
                conn.close()

    # 2. Sync JSONL session headers with strictly atomic file replacement
    updated_files = 0
    failed_files = []
    if sessions_dir.exists():
        for root, _, files in os.walk(str(sessions_dir)):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                file_path = os.path.join(root, f)
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
                        needs_write = True

                    # Sanitize session_meta if it contains harness instruction fields
                    meta, meta_changed = sanitize_session_item(meta)
                    if meta_changed or needs_write:
                        lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"
                        needs_write = True

                    # Process lines 1..N with schema-aware parsing
                    cleaned_lines = [lines[0]]
                    for l_idx in range(1, len(lines)):
                        orig_l = lines[l_idx]
                        l_str = orig_l.strip()
                        if not l_str:
                            cleaned_lines.append(orig_l)
                            continue

                        try:
                            item_data = json.loads(l_str)
                        except Exception:
                            # Preserve unparseable lines as-is without crashing
                            cleaned_lines.append(orig_l)
                            continue

                        # If syncing to OpenAI, drop synthetic cpa- reasoning carriers
                        if target_provider == "openai":
                            if item_data.get("type") == "response_item":
                                p = item_data.get("payload")
                                if isinstance(p, dict) and p.get("type") == "reasoning":
                                    enc = p.get("encrypted_content")
                                    if enc and isinstance(enc, str) and ("cpa-" in enc or enc.startswith("cpa-")):
                                        needs_write = True
                                        continue

                        # Sanitize structured harness/developer messages (preserves user/assistant/tool messages)
                        item_data, item_changed = sanitize_session_item(item_data)
                        if item_changed:
                            cleaned_lines.append(json.dumps(item_data, ensure_ascii=False) + "\n")
                            needs_write = True
                        else:
                            cleaned_lines.append(orig_l)

                    if needs_write:
                        lines = cleaned_lines
                        # Strictly atomic write: NO r+ in-place fallback
                        atomic_write_lines(file_path, lines)
                        updated_files += 1

                except Exception as e:
                    failed_files.append((file_path, str(e)))

    if failed_files:
        had_errors = True
        error(f"{len(failed_files)} session file(s) failed to sync")
        for fp, err in failed_files:
            info(f"  {os.path.basename(fp)}: {err}")

    # 2.5. Re-align lineage cutoff offsets for forked child sessions
    realigned_lineages = 0
    if sessions_dir.exists():
        try:
            realigned_lineages, realign_errors = store_realign_lineages(sessions_dir)
            if realign_errors > 0:
                had_errors = True
        except Exception as e:
            had_errors = True
            error(f"failed to realign lineages: {e}")

    # 3. Invalidate/clear stale thread_history projection cache if files/threads were updated
    if updated_files > 0 or updated_threads > 0 or realigned_lineages > 0:
        if not clear_history_projection_cache(codex_dir):
            had_errors = True

    msg = f"synced {updated_threads} thread(s), {updated_files} session file(s)"
    if realigned_lineages > 0:
        msg += f", realigned {realigned_lineages} forked lineage(s)"
    msg += f" -> '{target_provider}'"
    info(msg)
    return 1 if had_errors else 0


def verify_provider(
    target_provider: str,
    codex_dir: Path,
    check_instructions: bool = False,
    check_lineage: bool = False,
) -> int:
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        error(f"invalid provider '{target_provider}' (expected custom|openai)")
        return 2

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == f"verify-{target_provider}":
        info(f"injected failure at verify-{target_provider}")
        return 1

    db_path = codex_dir / "state_5.sqlite"
    sessions_dir = codex_dir / "sessions"

    errors: List[str] = []
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

    # 2. Verify JSONL files & harness instructions
    if sessions_dir.exists():
        for root, _, files in os.walk(str(sessions_dir)):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                checked_files += 1
                file_path = os.path.join(root, f)
                try:
                    with open(file_path, "r", encoding="utf-8") as sfile:
                        all_lines = sfile.readlines()
                    if not all_lines:
                        errors.append(f"Empty session file: {file_path}")
                        continue
                    meta = json.loads(all_lines[0])
                    if not isinstance(meta, dict) or "payload" not in meta or not isinstance(meta["payload"], dict):
                        errors.append(f"Invalid schema in session file: {file_path}")
                        continue
                    current_prov = meta["payload"].get("model_provider")
                    if current_prov != target_provider:
                        errors.append(f"Session {f} has model_provider='{current_prov}' (Expected '{target_provider}')")

                    if check_instructions:
                        for line_idx, raw_l in enumerate(all_lines):
                            l_strip = raw_l.strip()
                            if not l_strip:
                                continue
                            try:
                                l_item = json.loads(l_strip)
                                if has_unsanitized_fingerprint(l_item):
                                    errors.append(f"Session {f} line {line_idx + 1} contains un-sanitized 'based on GPT-5' in harness developer/system instructions")
                                    break
                            except Exception:
                                pass
                except Exception as e:
                    errors.append(f"Failed to verify {file_path}: {e}")

        # 3. Optional lineage integrity verification
        if check_lineage:
            lineage_issues = verify_lineage_integrity(sessions_dir)
            for issue in lineage_issues:
                errors.append(issue)

    if errors:
        error(f"verification failed: {len(errors)} issue(s)")
        for err in errors:
            info(f"  {err}")
        return 1

    info(f"verified {checked_threads} thread(s), {checked_files} session file(s) match '{target_provider}'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agents in Chat - Codex Session History Sync & Verify Helper")
    parser.add_argument("provider", nargs="?", help="Target model provider: 'custom' or 'openai'")
    parser.add_argument("--verify", action="store_true", help="Run in verification mode without modifying files")
    parser.add_argument("--check-instructions", action="store_true", help="Also verify that developer instructions contain no unsanitized fingerprints")
    parser.add_argument("--check-lineage", action="store_true", help="Also verify that forked lineage offsets are strictly aligned with parent ordinals")
    parser.add_argument("--codex-dir", default=None, help="Custom path to .codex directory (for tests / isolation)")

    args, remaining = parser.parse_known_args()

    is_verify = args.verify
    provider = args.provider
    if provider and provider.lower() == "verify" and remaining:
        is_verify = True
        provider = remaining[0]
    elif not provider and remaining:
        provider = remaining[0]

    if not provider:
        error("usage: sync_sessions.py [--verify] [--check-instructions] [--check-lineage] <custom|openai> [--codex-dir <path>]")
        return 2

    codex_dir = get_codex_dir(args.codex_dir)

    if is_verify:
        return verify_provider(
            provider,
            codex_dir,
            check_instructions=args.check_instructions,
            check_lineage=args.check_lineage,
        )
    else:
        return sync_provider(provider, codex_dir)


if __name__ == "__main__":
    sys.exit(main())
