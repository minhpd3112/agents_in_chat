import os, sys, sqlite3, json

def test_sqlite_and_sessions():
    # 1. Check state_5.sqlite
    db_path = os.path.expanduser("~/.codex/state_5.sqlite")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM threads WHERE model_provider != 'custom' AND model_provider IS NOT NULL;")
            non_custom_count = c.fetchone()[0]
            conn.close()
            if non_custom_count > 0:
                return False, f"Found {non_custom_count} threads in state_5.sqlite with non-custom provider (Need sync to 'custom')"
        except Exception as e:
            return False, f"Error reading state_5.sqlite: {e}"
    
    # 2. Check session headers in ~/.codex/sessions
    sessions_dir = os.path.expanduser("~/.codex/sessions")
    checked_files = 0
    if os.path.exists(sessions_dir):
        for root, _, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    checked_files += 1
                    p = os.path.join(root, f)
                    try:
                        with open(p, "r", encoding="utf-8") as sfile:
                            first_line = sfile.readline()
                        if first_line:
                            d = json.loads(first_line)
                            p_val = d.get("payload", {})
                            if isinstance(p_val, dict) and p_val.get("model_provider") not in ["custom", None]:
                                return False, f"Session {f} header has model_provider='{p_val.get('model_provider')}' (Expected 'custom')"
                    except Exception:
                        pass
    
    return True, f"SQLite threads and {checked_files} session headers all sync to model_provider='custom'."

if __name__ == "__main__":
    ok, msg = test_sqlite_and_sessions()
    print(f"[{'PASS' if ok else 'FAIL'}] SQLite & Sessions: {msg}")
    sys.exit(0 if ok else 1)
