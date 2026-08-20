import sys, os, sqlite3, json

def sync_provider(target_provider):
    codex_dir = os.path.expanduser("~/.codex")
    db_path = os.path.join(codex_dir, "state_5.sqlite")
    sessions_dir = os.path.join(codex_dir, "sessions")
    
    # 1. Sync SQLite threads table
    updated_threads = 0
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            # Check if threads table exists
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threads';")
            if c.fetchone():
                c.execute("UPDATE threads SET model_provider = ? WHERE model_provider IS NOT NULL;", (target_provider,))
                updated_threads = c.rowcount
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"[warn] Failed to update state_5.sqlite: {e}")
            
    # 2. Sync JSONL session headers
    updated_files = 0
    if os.path.exists(sessions_dir):
        for root, _, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    file_path = os.path.join(root, f)
                    try:
                        with open(file_path, "r", encoding="utf-8") as sfile:
                            lines = sfile.readlines()
                        if lines:
                            meta = json.loads(lines[0])
                            if "payload" in meta and isinstance(meta["payload"], dict):
                                if meta["payload"].get("model_provider") != target_provider:
                                    meta["payload"]["model_provider"] = target_provider
                                    lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"
                                    with open(file_path, "w", encoding="utf-8") as sfile:
                                        sfile.writelines(lines)
                                    updated_files += 1
                    except Exception as e:
                        pass
                        
    print(f"Synced {updated_threads} SQLite threads and {updated_files} session files to provider '{target_provider}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_sessions.py <custom|openai>")
        sys.exit(1)
    sync_provider(sys.argv[1].strip().lower())
