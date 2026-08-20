import os, sys, yaml

def test_config_yaml():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
    if not os.path.exists(config_path):
        return False, f"File {config_path} does not exist!"
    
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    # 1. Check routing & retries (Issue 1: Hang 50s)
    routing = data.get("routing", {})
    if routing.get("strategy") != "round-robin":
        return False, f"routing.strategy is '{routing.get('strategy')}', must be 'round-robin'"
    if data.get("request-retry") != 1:
        return False, f"request-retry is {data.get('request-retry')}, must be 1"
    if data.get("max-retry-credentials") != 4:
        return False, f"max-retry-credentials is {data.get('max-retry-credentials')}, must be 4"
    
    # 2. Check model aliases & force-mapping (Issue 2: Unknown provider)
    aliases = data.get("oauth-model-alias", {}).get("antigravity", [])
    alias_map = {item.get("name"): item for item in aliases}
    
    for req_name, exp_alias in [
        ("claude-sonnet-4-6", "claude-sonnet-4.6-thinking"),
        ("claude-opus-4-6-thinking", "claude-opus-4.6-thinking"),
        ("gemini-3.7-flash-high", "gemini-3.7-flash")
    ]:
        if req_name not in alias_map:
            return False, f"Missing alias mapping for {req_name}"
        if alias_map[req_name].get("alias") != exp_alias:
            return False, f"Alias for {req_name} is '{alias_map[req_name].get('alias')}', expected '{exp_alias}'"
        if not alias_map[req_name].get("force-mapping"):
            return False, f"force-mapping is not true for {req_name}"
    
    return True, "config.yaml routing, retry policies and model aliases are 100% compliant."

if __name__ == "__main__":
    ok, msg = test_config_yaml()
    print(f"[{'PASS' if ok else 'FAIL'}] Config YAML: {msg}")
    sys.exit(0 if ok else 1)
