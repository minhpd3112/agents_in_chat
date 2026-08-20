#!/usr/bin/env python3
import sys
import os
import sqlite3
import json

VALID_PROVIDERS = {"custom", "openai"}

def sync_provider(target_provider: str) -> int:
    # 1. Strict Validation
    target_provider = target_provider.strip().lower()
    if target_provider not in VALID_PROVIDERS:
        print(f"[ERROR] Provider khong hop le: '{target_provider}'. Chi chap nhan 'custom' hoac 'openai'.")
        return 2

    codex_dir = os.path.expanduser("~/.codex")
    db_path = os.path.join(codex_dir, "state_5.sqlite")
    sessions_dir = os.path.join(codex_dir, "sessions")
    
    had_errors = False

    # 2. Sync SQLite threads table
    updated_threads = 0
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threads';")
            if c.fetchone():
                c.execute("UPDATE threads SET model_provider = ? WHERE model_provider IS NOT NULL;", (target_provider,))
                updated_threads = c.rowcount
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ERROR] Failed to update state_5.sqlite: {e}")
            had_errors = True
            
    # 3. Sync JSONL session headers with Atomic file replace
    updated_files = 0
    failed_files = []
    if os.path.exists(sessions_dir):
        for root, _, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    file_path = os.path.join(root, f)
                    tmp_file_path = f"{file_path}.tmp.{os.getpid()}"
                    try:
                        with open(file_path, "r", encoding="utf-8") as sfile:
                            lines = sfile.readlines()
                        if lines:
                            meta = json.loads(lines[0])
                            if "payload" in meta and isinstance(meta["payload"], dict):
                                if meta["payload"].get("model_provider") != target_provider:
                                    meta["payload"]["model_provider"] = target_provider
                                    lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"
                                    # Atomic replace: write to tmp, flush, fsync, replace
                                    with open(tmp_file_path, "w", encoding="utf-8") as tmp_file:
                                        tmp_file.writelines(lines)
                                        tmp_file.flush()
                                        os.fsync(tmp_file.fileno())
                                    os.replace(tmp_file_path, file_path)
                                    updated_files += 1
                    except Exception as e:
                        print(f"[ERROR] Failed to update session file {file_path}: {e}")
                        failed_files.append((file_path, str(e)))
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

    print(f"Synced {updated_threads} SQLite threads and {updated_files} session files to provider '{target_provider}'.")
    return 1 if had_errors else 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_sessions.py <custom|openai>")
        sys.exit(1)
    sys.exit(sync_provider(sys.argv[1]))
