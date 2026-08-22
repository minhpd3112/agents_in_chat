import json, os, sys, stat

def test_models_cache():
    cache_path = os.path.expanduser("~/.codex/models_cache.json")
    if not os.path.exists(cache_path):
        return False, f"File {cache_path} does not exist!"
    
    # 1. Check BOM
    with open(cache_path, "rb") as f:
        head = f.read(3)
        if head == b"\xef\xbb\xbf":
            return False, "File contains UTF-8 BOM! (Codex CLI serde cannot parse BOM)"
    
    # 2. Check Read-Only attribute (Blocks Codex CLI RefreshStrategy::Online overwrite)
    file_mode = os.stat(cache_path).st_mode
    is_read_only = not bool(file_mode & stat.S_IWRITE)
    if not is_read_only:
        return False, "models_cache.json is NOT marked Read-Only! (Risk of ETag online overwrite)"
    
    # 3. Check JSON schema
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    if data.get("fetched_at") != "2099-01-01T00:00:00Z":
        return False, f"TTL not locked to 2099! Current: {data.get('fetched_at')}"
    
    models = {m.get('slug'): m for m in data.get('models', [])}
    for slug in ['gemini-3.7-flash', 'claude-sonnet-4.6-thinking', 'claude-opus-4.6-thinking', 'gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol', 'ox-alpha']:
        if slug not in models:
            return False, f"Missing model: {slug}"
        m = models[slug]
        if m.get('visibility') != 'list':
            return False, f"{slug} visibility is '{m.get('visibility')}', must be 'list' to appear in picker!"
        if 'gemini' in slug or 'claude' in slug:
            if m.get('tool_mode') != 'direct':
                return False, f"{slug} tool_mode is '{m.get('tool_mode')}' (must be 'direct')"
        if 'claude' in slug or slug == 'gemini-3.7-flash':
            if m.get('default_reasoning_level') != 'high':
                return False, f"{slug} default_reasoning_level is '{m.get('default_reasoning_level')}' (expected 'high')"
            efforts = [r.get('effort') for r in m.get('supported_reasoning_levels', [])]
            if 'xhigh' in efforts:
                return False, f"{slug} contains 'xhigh' which was requested to be removed!"
        if slug == 'ox-alpha':
            efforts = [r.get('effort') for r in m.get('supported_reasoning_levels', [])]
            if 'max' not in efforts:
                return False, f"ox-alpha missing 'max' reasoning effort! Current: {efforts}"
            if m.get('default_reasoning_level') != 'max':
                return False, f"ox-alpha default_reasoning_level is '{m.get('default_reasoning_level')}' (expected 'max')"
    
    return True, f"Valid models_cache.json with {len(models)} models (visibility='list'), Read-Only LOCKED, TTL 2099, No BOM."

if __name__ == "__main__":
    ok, msg = test_models_cache()
    print(f"[{'PASS' if ok else 'FAIL'}] Models Cache: {msg}")
    sys.exit(0 if ok else 1)
