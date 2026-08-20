# ==============================================================================
#  agents_in_chat: Factory Reset / Uninstaller for Windows (PowerShell)
#  Khoi phuc 100% cai dat goc, Lich su chat & Go bo lenh 'aic' khoi PATH
# ==============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }

$CodexDir = Join-Path $env:USERPROFILE ".codex"
$ModelsCachePath = Join-Path $CodexDir "models_cache.json"

Write-Host "`n=== Khoi phuc cai dat goc OpenAI Codex CLI ===" -ForegroundColor Cyan

# 1. Tat tien trinh proxy
& (Join-Path $ScriptDir "stop.ps1")

# 2. Mo khoa va xoa models_cache.json tuy chinh de Codex CLI tu tao lai 5 model goc cua OpenAI
if (Test-Path $ModelsCachePath) {
    Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    Remove-Item -Path $ModelsCachePath -Force -ErrorAction SilentlyContinue
    Write-Host "-> Da xoa models_cache.json tuy chinh (Codex se tu dung 5 model mac dinh cua OpenAI)." -ForegroundColor Green
}

# 3. Lam sach config.toml ve OpenAI mac dinh
$ConfigScript = Join-Path $ScriptDir "scripts\configure_codex_toml.py"
if (Test-Path $ConfigScript) {
    python $ConfigScript openai
}

# 4. Dong bo toan bo lich su chat ve provider 'openai'
$SyncScript = Join-Path $ScriptDir "scripts\sync_sessions.py"
if (Test-Path $SyncScript) {
    python $SyncScript openai
}

# 5. Go bo lenh 'aic' khoi User PATH & PowerShell Profile
$BinDir = Join-Path $ScriptDir "bin"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -like "*$BinDir*") {
    $NewUserPath = ($UserPath.Split(';') | Where-Object { $_ -ne $BinDir -and $_ -ne "" }) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    Write-Host "-> Da go $BinDir khoi User PATH." -ForegroundColor Green
}
if (Test-Path $PROFILE) {
    $pContent = Get-Content $PROFILE -Raw
    $cleaned = $pContent -replace '(?m)^function global:aic.*$', ''
    Set-Content -Path $PROFILE -Value $cleaned.Trim() -Encoding utf8
    Write-Host "-> Da go ham 'aic' khoi PowerShell Profile." -ForegroundColor Green
}


Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "   DA KHOI PHUC CAI DAT GOC & GO BO 'aic' THANH CONG 100%!" -ForegroundColor Green
Write-Host "============================================================`n" -ForegroundColor Green
