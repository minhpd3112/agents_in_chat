import os, sys, re
from pathlib import Path

def get_port():
    if os.environ.get("AIC_PORT"):
        try:
            return int(os.environ["AIC_PORT"])
        except ValueError:
            pass
    cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    if cfg.exists():
        try:
            m = re.search(r"^port:\s*(\d+)", cfg.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                return int(m.group(1))
        except Exception:
            pass
    return 8090

def test_codex_config():
    codex_dir = os.environ.get("AIC_CODEX_DIR") or os.environ.get("CODEX_DIR") or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    config_path = os.path.join(codex_dir, "config.toml")
    if not os.path.exists(config_path):
        return False, f"File {config_path} does not exist!"
    
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    if "model_provider = \"custom\"" not in content and 'model_provider = "custom"' not in content:
        return False, "model_provider is not set to 'custom' in config.toml"
    
    if "[model_providers.custom]" not in content:
        return False, "[model_providers.custom] section is missing"
    
    port = get_port()
    expected_url = f"http://127.0.0.1:{port}/v1"
    if expected_url not in content:
        return False, f"base_url does not point to {expected_url}"
    
    return True, "Codex config.toml is properly configured to use CLIProxyAPI."

if __name__ == "__main__":
    ok, msg = test_codex_config()
    print(f"[{'PASS' if ok else 'FAIL'}] Codex Config: {msg}")
    sys.exit(0 if ok else 1)
