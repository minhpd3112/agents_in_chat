#!/usr/bin/env python3
"""Codex session history provider sync & verify helper.

Usage:
    python sync_sessions.py [--verify] <custom|openai> [--codex-dir <path>]

Status goes to stderr so stdout stays clean for scripting.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from log_utils import error, info

VALID_PROVIDERS = {"custom", "openai"}

def get_codex_dir(custom_path=None) -> Path:
    if custom_path:
        return Path(custom_path).resolve()
    env_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def find_ordinal_byte_offset(parent_path: str, end_ordinal_exclusive: int) -> int:
    """Find the byte offset in parent_path where all ordinals < end_ordinal_exclusive end.
    If the file ends before reaching end_ordinal_exclusive, returns the file's total byte length."""
    file_size = os.path.getsize(parent_path)
    if end_ordinal_exclusive <= 0:
        return 0

    offset = 0
    matched_offset = file_size

    with open(parent_path, "rb") as pf:
        for line in pf:
            line_len = len(line)
            try:
                obj = json.loads(line.decode("utf-8"))
                ord_val = obj.get("ordinal")
                if ord_val is not None:
                    if ord_val < end_ordinal_exclusive:
                        matched_offset = offset + line_len
                    else:
                        break
            except Exception:
                pass
            offset += line_len

    return matched_offset


def realign_forked_lineages(sessions_dir: Path) -> int:
    """Scans all session JSONL files and repairs history_base.end_byte_offset
    if an ancestor rollout shrank due to carrier sanitization.
    Returns count of realigned sessions."""
    if not sessions_dir.exists():
        return 0

    thread_map = {}
    for root, _, files in os.walk(str(sessions_dir)):
        for f in files:
            if f.endswith(".jsonl"):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8") as sf:
                        first = sf.readline()
                    if not first:
                        continue
                    meta = json.loads(first)
                    payload = meta.get("payload", {})
                    tid = payload.get("id") or payload.get("session_id")
                    if tid and tid not in thread_map:
                        thread_map[tid] = fp
                except Exception:
                    pass

    realigned_count = 0
    for root, _, files in os.walk(str(sessions_dir)):
        for f in files:
            if f.endswith(".jsonl"):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8") as sf:
                        lines = sf.readlines()
                    if not lines:
                        continue
                    meta = json.loads(lines[0])
                    payload = meta.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    hbase = payload.get("history_base")
                    if not isinstance(hbase, dict):
                        continue

                    parent_id = hbase.get("thread_id")
                    cutoff = hbase.get("end_byte_offset")
                    end_ord = hbase.get("end_ordinal_exclusive")
                    if not parent_id or cutoff is None or parent_id not in thread_map:
                        continue

                    parent_fp = thread_map[parent_id]
                    parent_size = os.path.getsize(parent_fp)

                    if cutoff > parent_size:
                        new_cutoff = find_ordinal_byte_offset(parent_fp, end_ord) if end_ord is not None else parent_size
                        new_cutoff = min(new_cutoff, parent_size)
                        hbase["end_byte_offset"] = new_cutoff
                        lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"

                        tmp_path = f"{fp}.tmp.{os.getpid()}_{time.time_ns()}"
                        try:
                            with open(tmp_path, "w", encoding="utf-8") as tf:
                                tf.writelines(lines)
                                tf.flush()
                                os.fsync(tf.fileno())
                            os.replace(tmp_path, fp)
                        except PermissionError:
                            with open(fp, "r+", encoding="utf-8") as tf:
                                tf.seek(0)
                                tf.writelines(lines)
                                tf.truncate()
                                tf.flush()
                                os.fsync(tf.fileno())
                        finally:
                            if os.path.exists(tmp_path):
                                try:
                                    os.remove(tmp_path)
                                except Exception:
                                    pass

                        realigned_count += 1
                        info(f"realigned lineage for {f}: cutoff {cutoff} -> {new_cutoff} (parent: {os.path.basename(parent_fp)})")
                except Exception as e:
                    info(f"warning: failed to realign lineage in {f}: {e}")

    return realigned_count


def clear_history_projection_cache(codex_dir: Path) -> bool:
    """Invalidate stale thread_history projection cache with lock retries."""
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
        error(f"{len(failed_files)} session file(s) failed to sync")
        for fp, err in failed_files:
            info(f"  {os.path.basename(fp)}: {err}")

    # 2.5. Re-align lineage cutoff offsets for forked child sessions if ancestors shrank
    realigned_lineages = 0
    if sessions_dir.exists():
        realigned_lineages = realign_forked_lineages(sessions_dir)

    # 3. Invalidate/clear stale thread_history projection cache if files/threads were updated
    if updated_files > 0 or updated_threads > 0 or realigned_lineages > 0:
        clear_history_projection_cache(codex_dir)

    msg = f"synced {updated_threads} thread(s), {updated_files} session file(s)"
    if realigned_lineages > 0:
        msg += f", realigned {realigned_lineages} forked lineage(s)"
    msg += f" -> '{target_provider}'"
    info(msg)
    return 1 if had_errors else 0


def verify_provider(target_provider: str, codex_dir: Path) -> int:
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        error(f"invalid provider '{target_provider}' (expected custom|openai)")
        return 2

    if os.environ.get("AIC_TEST_MODE") == "1" and os.environ.get("AIC_FAIL_STEP") == f"verify-{target_provider}":
        info(f"injected failure at verify-{target_provider}")
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

    # 2. Verify JSONL files & Lineage bounds
    if sessions_dir.exists():
        thread_sizes = {}
        # First pass: record all thread file sizes
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
                        tid = meta["payload"].get("id") or meta["payload"].get("session_id")
                        if tid and tid not in thread_sizes:
                            thread_sizes[tid] = os.path.getsize(file_path)
                    except Exception as e:
                        errors.append(f"Failed to verify {file_path}: {e}")

        # Second pass: verify all history_base cutoff bounds
        for root, _, files in os.walk(str(sessions_dir)):
            for f in files:
                if f.endswith(".jsonl"):
                    file_path = os.path.join(root, f)
                    try:
                        with open(file_path, "r", encoding="utf-8") as sfile:
                            first_line = sfile.readline()
                        if first_line:
                            meta = json.loads(first_line)
                            hbase = meta.get("payload", {}).get("history_base")
                            if isinstance(hbase, dict):
                                ptid = hbase.get("thread_id")
                                cutoff = hbase.get("end_byte_offset")
                                if ptid in thread_sizes and cutoff is not None:
                                    parent_size = thread_sizes[ptid]
                                    if cutoff > parent_size:
                                        errors.append(f"Session {f} has cutoff byte offset {cutoff} > parent size {parent_size}")
                    except Exception:
                        pass

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
        error("usage: sync_sessions.py [--verify] <custom|openai> [--codex-dir <path>]")
        return 2

    codex_dir = get_codex_dir(args.codex_dir)

    if is_verify:
        return verify_provider(provider, codex_dir)
    else:
        return sync_provider(provider, codex_dir)


if __name__ == "__main__":
    sys.exit(main())
