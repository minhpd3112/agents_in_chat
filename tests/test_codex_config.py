import os, sys

def test_codex_config():
    config_path = os.path.expanduser("~/.codex/config.toml")
    if not os.path.exists(config_path):
        return False, f"File {config_path} does not exist!"
    
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    if "model_provider = \"custom\"" not in content and 'model_provider = "custom"' not in content:
        return False, "model_provider is not set to 'custom' in config.toml"
    
    if "[model_providers.custom]" not in content:
        return False, "[model_providers.custom] section is missing"
    
    if "http://127.0.0.1:8080/v1" not in content:
        return False, "base_url does not point to http://127.0.0.1:8080/v1"
    
    return True, "Codex config.toml is properly configured to use CLIProxyAPI."

if __name__ == "__main__":
    ok, msg = test_codex_config()
    print(f"[{'PASS' if ok else 'FAIL'}] Codex Config: {msg}")
    sys.exit(0 if ok else 1)
