import sys, os, time

sys.path.insert(0, os.path.dirname(__file__))
from test_proxy_health import test_proxy_health
from test_config_yaml import test_config_yaml
from test_models_cache import test_models_cache
from test_codex_config import test_codex_config
from test_sqlite_and_sessions import test_sqlite_and_sessions
from test_gemini_tool_calling import test_gemini_tool_calling
from test_claude_tool_calling import test_claude_tool_calling

def run_all():
    print("=" * 70)
    print("      AGENTS_IN_CHAT COMPREHENSIVE REGRESSION TEST SUITE")
    print("=" * 70)
    
    suites = [
        ("1. Proxy Health & Port 8080", test_proxy_health),
        ("2. Config YAML (Routing, Retries & Aliases)", test_config_yaml),
        ("3. Models Cache (BOM Check, TTL 2099 & Tool Specs)", test_models_cache),
        ("4. Codex config.toml Integrity", test_codex_config),
        ("5. SQLite DB & Session Headers Provider Sync", test_sqlite_and_sessions),
        ("6. Gemini 3.7 Flash Native Tool Calling (No Silence)", test_gemini_tool_calling),
        ("7. Claude Sonnet 4.6 Native Tool Calling (No Hallucination)", test_claude_tool_calling),
    ]
    
    passed = 0
    start_time = time.time()
    
    for name, test_fn in suites:
        print(f"\nRunning: {name}...")
        try:
            ok, msg = test_fn()
            if ok:
                passed += 1
                print(f"  --> [PASS] {msg}")
            else:
                print(f"  --> [FAIL] {msg}")
        except Exception as e:
            print(f"  --> [ERROR] Exception occurred: {e}")
    
    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print(f"TEST SUMMARY: {passed}/{len(suites)} suites passed in {elapsed:.2f}s")
    print("=" * 70)
    
    if passed == len(suites):
        print("ALL TESTS PASSED! Entire system is 100% robust and regression-free.\n")
        return 0
    else:
        print("SOME TESTS FAILED! Please inspect the failures above.\n")
        return 1

if __name__ == "__main__":
    sys.exit(run_all())
