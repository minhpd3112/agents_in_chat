# AGENTS.md

## What this repo is

- AIC ("Agents in Chat"): multi-account OAuth quota pool behind a local reverse proxy (`cli-proxy-api`, prebuilt binary at repo root, port 127.0.0.1:8090) that OpenAI Codex CLI points at via `~/.codex/config.toml` (`base_url = http://127.0.0.1:8090/v1`, `wire_api = "responses"`).
- Not a buildable app: Python >=3.8 **stdlib-only** scripts + PowerShell/bash installers wrapped around the vendored binary. The binary is gitignored — never commit or attempt to rebuild it.
- Before debugging proxy/session issues, check `docs/KIEN_TRUC_VA_XU_LY_LOI.md` — it documents ~17 known failure modes and their fixes.

## Commands

- Tests: `python tests/run_tests.py` (or `aic test`). 9 suites; suites 1 (proxy health) and 8–9 (live Gemini/Claude tool calling over real upstreams) require the proxy running first: `python bin/aic.py start`.
- `tests/test_install_uninstall_integration.py` is NOT wired into `run_tests.py` — run it directly when changing installer/rollback logic.
- No lint/typecheck/build/codegen steps exist. Don't invent them.

## Critical: isolate tests from real user state

- Installer and helper scripts mutate the REAL `~/.codex` (config.toml, `models_cache.json`, SQLite DBs, chat session history). When exercising install/uninstall/sync code, always set `AIC_TEST_MODE=1` plus `AIC_CODEX_DIR`, `AIC_PROFILE_PATH`, `AIC_USER_PATH_FILE` pointed into temp dirs; `AIC_SKIP_DOWNLOAD=1`, `AIC_SKIP_PROXY=1`; `AIC_FAIL_STEP=<step>` injects failures to exercise rollback paths.
- Both `scripts/configure_codex_toml.py` and `scripts/sync_sessions.py` honor `AIC_CODEX_DIR`/`CODEX_DIR`/`CODEX_HOME`.

## Hard-won gotchas

- **UTF-8 BOM breaks Codex CLI**: PowerShell 5.1 default JSON writes prepend a BOM that crashes the Rust serde parser. Write JSON via Python or `[System.IO.File]::WriteAllText($p, $t, [System.Text.UTF8Encoding]::new($false))`.
- `~/.codex/models_cache.json` is deliberately OS-locked read-only to block Codex CLI's ETag overwrite; its `client_version` must match the installed codex version (installer derives it from `codex --version`). Only edit the template `docs/models_cache_template.json`, keeping per-model required flags: `"visibility": "list"`, `apply_patch_tool_type: "freeform"`, `tool_mode: "direct"`, `instructions_template`.
- `sync_sessions.py` does more than flip provider tags: syncing back to `openai` strips synthetic `cpa-*` encrypted reasoning blobs (else OpenAI returns HTTP 400 `invalid_encrypted_content`) and clears `thread_history_1.sqlite` projection caches after rewriting `.jsonl` (else `/fork` fails with ordinal-gap errors).
- `scripts/backup_auths.py` is intentionally event-driven (restore before proxy start, backup after stop) — no daemon, no polling. It only snapshots valid JSON (>0 bytes, no NUL bytes) to defend against NTFS zero-byte token corruption.
- All file mutations follow atomic temp-file + `os.replace()`; `config.toml` backup/restore is byte-exact with a SHA256 manifest under `~/.codex/aic-backup/`. Preserve these patterns.
- Tests and installers assert exit codes and file states only — they never parse script stdout — so log text/stream changes are safe as long as return codes stay identical.

## Secrets & gitignored paths

- `config.yaml` (real config, contains a bcrypt secret-key), `auths/`, `auths_backup/`, `auths_disabled/` (live OAuth tokens), `*.sqlite`, `sessions/`, and `ma_nguon_tham_khao/` (scratch/reference repos) are all gitignored. Reference config lives in `config.example.yaml`. Never commit any of these.

## Language conventions

- Markdown docs and README are in Vietnamese — keep them Vietnamese when editing.
- Script log/status messages are concise English one-liners on **stderr** via the shared helper `scripts/log_utils.py` (`info`/`warn`/`error`) — stdout stays clean for scripting (precedent: `scripts/backup_auths.py`). Legacy UI panels (`aic status`, help text, login banners) still use Vietnamese without diacritics; don't add accents to them.
