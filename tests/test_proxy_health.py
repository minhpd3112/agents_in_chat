import urllib.request, json, sys, os, re
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

def test_proxy_health():
    port = get_port()
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return False, f"HTTP status {resp.status}"
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get('id') for m in data.get('data', [])]
            required = ['gemini-3.7-flash', 'claude-sonnet-4.6-thinking', 'gpt-5.6-luna']
            for r in required:
                if r not in models:
                    return False, f"Missing model {r} in /v1/models (found: {models})"
            return True, f"Proxy UP! {len(models)} models online: {', '.join(models)}"
    except Exception as e:
        return False, f"Cannot connect to proxy at {url}: {e}"

if __name__ == "__main__":
    ok, msg = test_proxy_health()
    print(f"[{'PASS' if ok else 'FAIL'}] Proxy Health: {msg}")
    sys.exit(0 if ok else 1)
