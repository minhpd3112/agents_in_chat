import os, re

def clean_and_write_config(target_provider="custom"):
    config_path = os.path.expanduser("~/.codex/config.toml")
    existing_text = ""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8-sig") as f:
            existing_text = f.read()

    # Extract [projects.*], [mcp_servers.*]
    projects_blocks = re.findall(r'(\[projects\.[^\]]+\]\s*trust_level\s*=\s*"[^"]+")', existing_text)
    mcp_blocks = re.findall(r'(\[mcp_servers\.[^\]]+\].*?)(?=\n\[|\Z)', existing_text, re.DOTALL)
    
    lines = []
    if target_provider == "custom":
        lines.append('model = "gemini-3.7-flash"')
        lines.append('model_reasoning_effort = "high"')
        lines.append('service_tier = "default"')
        lines.append('model_provider = "custom"')
        lines.append('')
        lines.append('[model_providers.custom]')
        lines.append('name = "Custom Quota Pool"')
        lines.append('base_url = "http://127.0.0.1:8080/v1"')
        lines.append('wire_api = "responses"')
        lines.append('')
    else:
        lines.append('model = "gpt-5.6-sol"')
        lines.append('model_reasoning_effort = "high"')
        lines.append('service_tier = "default"')
        lines.append('model_provider = "openai"')
        lines.append('')

    # Add windows sandbox
    lines.append('[windows]')
    lines.append('sandbox = "elevated"')
    lines.append('')

    # Add preserved projects
    for p in projects_blocks:
        lines.append(p.strip())
        lines.append('')

    # Add preserved mcp_servers
    for m in mcp_blocks:
        lines.append(m.strip())
        lines.append('')

    final_content = "\n".join(lines).strip() + "\n"
    
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    print(f"Cleaned config.toml for provider: '{target_provider}'!")

if __name__ == "__main__":
    import sys
    prov = sys.argv[1] if len(sys.argv) > 1 else "custom"
    clean_and_write_config(prov)
