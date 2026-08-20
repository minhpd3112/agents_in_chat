import os
import sys
import json
import sqlite3
import tempfile
import shutil
import subprocess
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def setup_integration_env(target_provider="openai", with_bom=False, with_lf=True, absent_config=False, custom_sections=True):
    tmp_root = Path(tempfile.mkdtemp(prefix="aic_int_test_"))
    codex_dir = tmp_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = codex_dir / "sessions" / "2026-08"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. DB
    db_path = codex_dir / "state_5.sqlite"
    conn = sqlite3.connect(str(db_path))
    with conn:
        c = conn.cursor()
        c.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, title TEXT);")
        c.execute("INSERT INTO threads VALUES ('t1', ?, 'Thread 1');", (target_provider,))
    conn.close()
    
    # 2. Session
    s_path = sessions_dir / "session_1.jsonl"
    header = {"type": "session_meta", "payload": {"id": "s1", "model_provider": target_provider}}
    msg = {"type": "message", "role": "user", "content": "hello"}
    with open(s_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        f.write(json.dumps(msg) + "\n")
        
    # 3. Config
    config_path = codex_dir / "config.toml"
    raw_config_bytes = b""
    if not absent_config:
        text = 'model = "gpt-5.6-sol"\nmodel_provider = "openai"\napproval_policy = "always"\n\n'
        if custom_sections:
            text += (
                '[profiles.personal]\nmodel = "gpt-5.6-sol"\nmodel_provider = "openai"\n\n'
                '[projects."/test"]\ntrust_level = "trusted"\n\n'
                '[mcp_servers.db]\ncommand = "npx"\n\n'
                '[windows]\nsandbox = "elevated"\n'
            )
        if with_bom:
            raw_config_bytes = b'\xef\xbb\xbf' + text.encode("utf-8")
        else:
            raw_config_bytes = text.encode("utf-8")
        if not with_lf:
            raw_config_bytes = raw_config_bytes.replace(b'\n', b'\r\n')
        config_path.write_bytes(raw_config_bytes)
        
    # 4. Profile & PATH & BinLink
    profile_path = tmp_root / "profile.ps1"
    user_path_file = tmp_root / "user_path.txt"
    bin_link_dir = tmp_root / "local_bin"
    bin_link_dir.mkdir(parents=True, exist_ok=True)
    
    env = os.environ.copy()
    env["AIC_TEST_MODE"] = "1"
    env["AIC_CODEX_DIR"] = str(codex_dir)
    env["AIC_PROFILE_PATH"] = str(profile_path)
    env["AIC_USER_PATH_FILE"] = str(user_path_file)
    env["AIC_BIN_LINK_DIR"] = str(bin_link_dir)
    env["AIC_SKIP_DOWNLOAD"] = "1"
    env["AIC_SKIP_PROXY"] = "1"
    
    return tmp_root, codex_dir, config_path, raw_config_bytes, profile_path, user_path_file, bin_link_dir, env


def run_ps1(script_name, env):
    script_path = ROOT_DIR / script_name
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def run_sh(script_name, env):
    script_path = ROOT_DIR / script_name
    bash_bin = "bash"
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.exists():
            bash_bin = str(git_bash)
    cmd = [bash_bin, str(script_path)]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def run_integration_tests(platform="windows"):
    print("=" * 70)
    print(f"  RUNNING INTEGRATION TESTS ({platform.upper()} SCRIPTS IN CHILD PROCESSES)")
    print("=" * 70)
    
    is_win = (platform.lower() == "windows")
    run_install = (lambda env: run_ps1("install.ps1", env)) if is_win else (lambda env: run_sh("install.sh", env))
    run_uninstall = (lambda env: run_ps1("uninstall.ps1", env)) if is_win else (lambda env: run_sh("uninstall.sh", env))
    
    passed = 0
    total = 15

    # Case 1: Windows/Unix install & uninstall success with LF & sections
    print("Case 1: Full Install & Uninstall success with LF, profiles, projects, mcp...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        r_inst = run_install(env)
        assert r_inst.returncode == 0, f"Install failed with output: {r_inst.stderr}\n{r_inst.stdout}"
        
        # Verify custom
        sync_verify = subprocess.run([sys.executable, str(ROOT_DIR / "scripts" / "sync_sessions.py"), "--verify", "custom"], env=env, capture_output=True)
        assert sync_verify.returncode == 0
        
        # Verify backup exact bytes
        bak_file = codex_dir / "aic-backup" / "config.toml.bak"
        assert bak_file.read_bytes() == raw_orig
        
        # Uninstall
        r_uninst = run_uninstall(env)
        assert r_uninst.returncode == 0, f"Uninstall failed: {r_uninst.stderr}\n{r_uninst.stdout}"
        
        # Verify openai
        sync_verify_un = subprocess.run([sys.executable, str(ROOT_DIR / "scripts" / "sync_sessions.py"), "--verify", "openai"], env=env, capture_output=True)
        assert sync_verify_un.returncode == 0
        
        # Verify config restored byte-exact
        assert config_path.read_bytes() == raw_orig
        assert not (codex_dir / "models_cache.json").exists()
        assert not (codex_dir / "aic-backup").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 2: Config with UTF-8 BOM
    print("Case 2: Config with UTF-8 BOM backup and restore byte-for-byte...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env(with_bom=True)
    try:
        assert run_install(env).returncode == 0
        assert (codex_dir / "aic-backup" / "config.toml.bak").read_bytes() == raw_orig
        assert run_uninstall(env).returncode == 0
        assert config_path.read_bytes() == raw_orig
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 3: Config initially absent
    print("Case 3: Config initially absent -> manifest original_exists=false, uninstall deletes config...")
    tmp_root, codex_dir, config_path, _, prof, upath, _, env = setup_integration_env(absent_config=True)
    try:
        assert run_install(env).returncode == 0
        manifest = json.loads((codex_dir / "aic-backup" / "manifest.json").read_text())
        assert manifest["original_exists"] is False
        assert config_path.exists()
        assert run_uninstall(env).returncode == 0
        assert not config_path.exists(), "Config should have been deleted on uninstall"
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 4: Manifest corrupted or missing backup halts before modifying config
    print("Case 4: Corrupt manifest halts install before mutation...")
    tmp_root, codex_dir, config_path, raw_orig, _, _, _, env = setup_integration_env()
    try:
        bak_dir = codex_dir / "aic-backup"
        bak_dir.mkdir(parents=True, exist_ok=True)
        (bak_dir / "manifest.json").write_text("MALFORMED JSON", encoding="utf-8")
        
        r = run_install(env)
        assert r.returncode != 0
        assert config_path.read_bytes() == raw_orig
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 5: Sync install fails -> Rollback config, cache, PATH
    print("Case 5: Sync install failure triggers full rollback...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        env["AIC_FAIL_STEP"] = "sync-custom"
        r = run_install(env)
        assert r.returncode != 0
        assert config_path.read_bytes() == raw_orig
        assert not (codex_dir / "models_cache.json").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 6: Verify install fails -> Rollback
    print("Case 6: Verify install failure triggers full rollback...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        env["AIC_FAIL_STEP"] = "verify-custom"
        r = run_install(env)
        assert r.returncode != 0
        assert config_path.read_bytes() == raw_orig
        assert not (codex_dir / "models_cache.json").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 7: Start proxy fails after registering PATH/profile -> Full Rollback
    print("Case 7: Start proxy failure after PATH/Profile triggers complete rollback...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        env["AIC_FAIL_STEP"] = "start"
        r = run_install(env)
        assert r.returncode != 0
        assert config_path.read_bytes() == raw_orig
        assert not (codex_dir / "models_cache.json").exists()
        # Verify profile cleaned up
        if prof.exists():
            assert "# >>> AIC >>>" not in prof.read_text(encoding="utf-8")
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 8: Uninstall sync fails -> Abort, retain config/cache/PATH/backup
    print("Case 8: Uninstall sync failure aborts and preserves AIC config/cache/backup...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        # Install successfully first
        assert run_install(env).returncode == 0
        
        # Inject fail on sync-openai
        env["AIC_FAIL_STEP"] = "sync-openai"
        r = run_uninstall(env)
        assert r.returncode != 0
        # Config custom must still be in place
        assert 'model_provider = "custom"' in config_path.read_text(encoding="utf-8")
        assert (codex_dir / "models_cache.json").exists()
        assert (codex_dir / "aic-backup").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 9: Uninstall verify fails -> Abort
    print("Case 9: Uninstall verify failure aborts and preserves AIC state...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        assert run_install(env).returncode == 0
        env["AIC_FAIL_STEP"] = "verify-openai"
        r = run_uninstall(env)
        assert r.returncode != 0
        assert (codex_dir / "models_cache.json").exists()
        assert (codex_dir / "aic-backup").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 10: Restore config fails -> Abort
    print("Case 10: Restore config failure preserves cache/backup/profile...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        assert run_install(env).returncode == 0
        env["AIC_FAIL_STEP"] = "restore-config"
        r = run_uninstall(env)
        assert r.returncode != 0
        assert (codex_dir / "models_cache.json").exists()
        assert (codex_dir / "aic-backup").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 11: Remove cache fails -> Abort before deleting backup
    print("Case 11: Cache deletion failure aborts and retains backup...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        assert run_install(env).returncode == 0
        env["AIC_FAIL_STEP"] = "remove-cache"
        r = run_uninstall(env)
        assert r.returncode != 0
        assert (codex_dir / "aic-backup").exists()
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 12: Missing helper script fails at preflight
    print("Case 12: Missing helper script aborts in preflight before mutation...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        # Move sync_sessions temporarily or pass non-existent path
        sync_script = ROOT_DIR / "scripts" / "sync_sessions.py"
        sync_bak = ROOT_DIR / "scripts" / "sync_sessions.py.tmpbak"
        sync_script.rename(sync_bak)
        try:
            r = run_install(env)
            assert r.returncode != 0
            assert config_path.read_bytes() == raw_orig
        finally:
            if sync_bak.exists():
                sync_bak.rename(sync_script)
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 13: Legacy fallback preserves profiles and user configurations 100%
    print("Case 13: Legacy fallback preserves [profiles.*] and custom configurations...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        # Create a custom config without backup
        legacy_text = (
            'model = "gemini-3.7-flash"\nmodel_provider = "custom"\n\n'
            '[model_providers.custom]\nname = "Custom Quota Pool"\nbase_url = "http://127.0.0.1:8080/v1"\nwire_api = "responses"\n\n'
            '[profiles.personal]\nmodel = "gemini-3.7-flash"\nmodel_provider = "custom"\n\n'
            '[projects."/my-proj"]\ntrust_level = "trusted"\n\n'
            '[mcp_servers.db]\ncommand = "npx"\n'
        )
        config_path.write_bytes(legacy_text.encode("utf-8"))
        
        # Run uninstall without backup manifest
        r = run_uninstall(env)
        assert r.returncode == 0
        
        cleaned = config_path.read_bytes().decode("utf-8")
        assert 'model_provider = "openai"' in cleaned
        assert '[model_providers.custom]' not in cleaned
        # [profiles.personal] model and provider must NOT be changed
        assert '[profiles.personal]\nmodel = "gemini-3.7-flash"\nmodel_provider = "custom"' in cleaned
        assert '[projects."/my-proj"]' in cleaned
        assert '[mcp_servers.db]' in cleaned
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 14: Pre-existing aic command is protected
    print("Case 14: Pre-existing personal 'aic' command is protected from silent deletion...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, bin_link_dir, env = setup_integration_env()
    try:
        if is_win:
            prof.write_text('function global:aic { Write-Host "Personal User Tool" }\n', encoding="utf-8")
            assert run_install(env).returncode == 0
            # Both personal and AIC block can exist or personal function was preserved
            p_text = prof.read_text(encoding="utf-8")
            assert "Personal User Tool" in p_text
            assert run_uninstall(env).returncode == 0
            p_text_after = prof.read_text(encoding="utf-8")
            assert "Personal User Tool" in p_text_after
        else:
            pre_existing_bin = bin_link_dir / "aic"
            pre_existing_bin.write_text("#!/bin/sh\necho 'user tool'\n", encoding="utf-8")
            assert run_install(env).returncode == 0
            assert run_uninstall(env).returncode == 0
            assert pre_existing_bin.exists()
            assert "user tool" in pre_existing_bin.read_text(encoding="utf-8")
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Case 15: Idempotency (Install twice, Uninstall once)
    print("Case 15: Idempotency - install twice without corrupting initial backup...")
    tmp_root, codex_dir, config_path, raw_orig, prof, upath, _, env = setup_integration_env()
    try:
        assert run_install(env).returncode == 0
        bak_bytes_1 = (codex_dir / "aic-backup" / "config.toml.bak").read_bytes()
        assert bak_bytes_1 == raw_orig
        
        # Second install
        assert run_install(env).returncode == 0
        bak_bytes_2 = (codex_dir / "aic-backup" / "config.toml.bak").read_bytes()
        assert bak_bytes_2 == raw_orig, "Second install overwrote initial backup!"
        
        # Profile marker should only exist ONCE
        if prof.exists():
            assert prof.read_text(encoding="utf-8").count("# >>> AIC >>>") == 1
            
        assert run_uninstall(env).returncode == 0
        assert config_path.read_bytes() == raw_orig
        print("  -> [PASS]")
        passed += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print("\n" + "=" * 70)
    print(f"INTEGRATION TEST SUMMARY: {passed}/{total} passed (100% Green)")
    print("=" * 70)
    return passed == total

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="windows" if sys.platform == "win32" else "unix")
    args = parser.parse_args()
    
    ok = run_integration_tests(args.platform)
    sys.exit(0 if ok else 1)
