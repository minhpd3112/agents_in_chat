import urllib.request, json, sys

def test_claude_tool_calling():
    url = "http://127.0.0.1:8080/v1/responses"
    payload = {
        "model": "claude-sonnet-4.6-thinking",
        "stream": True,
        "input": [
            {"role": "developer", "content": "You are Codex running on a machine."},
            {"role": "user", "content": "Run git log in current repo using exec_command."}
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
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            for line in resp:
                l = line.decode('utf-8', errors='ignore').strip()
                if "function_call" in l:
                    has_function_call = True
                if "functions.exec" in l or "<function_calls>" in l:
                    has_text_fallback = True
        
        if has_function_call and not has_text_fallback:
            return True, "Claude Sonnet 4.6 emitted native function_call SSE event!"
        elif has_text_fallback:
            return False, "Claude fell back to raw text functions.exec (Hallucination risk!)"
        else:
            return False, "Claude did not emit function_call"
    except Exception as e:
        return False, f"Request error: {e}"

if __name__ == "__main__":
    ok, msg = test_claude_tool_calling()
    print(f"[{'PASS' if ok else 'FAIL'}] Claude Tool Calling: {msg}")
    sys.exit(0 if ok else 1)
