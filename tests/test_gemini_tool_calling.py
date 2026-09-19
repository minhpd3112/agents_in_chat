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

def test_gemini_tool_calling():
    port = get_port()
    url = f"http://127.0.0.1:{port}/v1/responses"
    payload = {
        "model": "gemini-3.8-flash",
        "stream": True,
        "input": [
            {"role": "developer", "content": "You are Codex running on a machine."},
            {"role": "user", "content": "Run git status in current repo using exec_command."}
        ],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Runs a command in the user shell.",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"]
                }
            }
        ]
    }
    
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
        has_function_call = False
        has_text_fallback = False
        
        with urllib.request.urlopen(req, timeout=25) as resp:
            for line in resp:
                l = line.decode('utf-8', errors='ignore').strip()
                if "function_call" in l:
                    has_function_call = True
                if "functions.exec" in l:
                    has_text_fallback = True
        
        if has_function_call and not has_text_fallback:
            return True, "Gemini 3.8 Flash emitted native function_call SSE event!"
        elif has_text_fallback:
            return False, "Gemini fell back to plain text functions.exec (Protocol defect!)"
        else:
            return False, "Gemini did not emit function_call (Turn swallowed/silent)"
    except Exception as e:
        return False, f"Request error: {e}"

if __name__ == "__main__":
    ok, msg = test_gemini_tool_calling()
    print(f"[{'PASS' if ok else 'FAIL'}] Gemini Tool Calling: {msg}")
    sys.exit(0 if ok else 1)
