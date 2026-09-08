# ==============================================================================
#  agents_in_chat: Factory Reset / Uninstaller for Windows (PowerShell)
#  Khoi phuc cai dat goc, Lich su chat & Go bo lenh 'aic' khoi PATH
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }

# 0. Test Mode & Environment Isolation
$IsTestMode = ($env:AIC_TEST_MODE -eq "1")
$CodexDir = if ($IsTestMode -and $env:AIC_CODEX_DIR) { (Resolve-Path $env:AIC_CODEX_DIR).Path } else { Join-Path $env:USERPROFILE ".codex" }
$ProfilePath = if ($IsTestMode -and $env:AIC_PROFILE_PATH) { $env:AIC_PROFILE_PATH } else { $PROFILE }
$UserPathFile = if ($IsTestMode -and $env:AIC_USER_PATH_FILE) { $env:AIC_USER_PATH_FILE } else { $null }
$SkipProxy = ($IsTestMode -and $env:AIC_SKIP_PROXY -eq "1")
$FailStep = if ($IsTestMode) { $env:AIC_FAIL_STEP } else { $null }

# 1. Tim kiem Python executable
$PythonExe = ""
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $PythonExe = "python3"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} else {
    Write-Error "Khong tim thay Python! Vui long cai dat Python truoc khi chay uninstall."
    exit 1
}

$ModelsCachePath = Join-Path $CodexDir "models_cache.json"
$ConfigScript = if ($IsTestMode -and $env:AIC_CONFIG_SCRIPT) { $env:AIC_CONFIG_SCRIPT } else { Join-Path $ScriptDir "scripts\configure_codex_toml.py" }
$SyncScript = if ($IsTestMode -and $env:AIC_SYNC_SCRIPT) { $env:AIC_SYNC_SCRIPT } else { Join-Path $ScriptDir "scripts\sync_sessions.py" }
$CheckCodexScript = if ($IsTestMode -and $env:AIC_CHECK_CODEX_SCRIPT) { $env:AIC_CHECK_CODEX_SCRIPT } else { Join-Path $ScriptDir "scripts\check_codex_running.py" }
$BinDir = (Resolve-Path (Join-Path $ScriptDir "bin") -ErrorAction SilentlyContinue).Path
if (-not $BinDir) { $BinDir = Join-Path $ScriptDir "bin" }

# Preflight: Mandatory Helper validation
if (-not (Test-Path $ConfigScript)) {
    Write-Error "Thieu helper bat buoc tai $ConfigScript"
    exit 1
}
if (-not (Test-Path $SyncScript)) {
    Write-Error "Thieu helper bat buoc tai $SyncScript"
    exit 1
}
if (-not (Test-Path $CheckCodexScript)) {
    Write-Error "Thieu helper bat buoc tai $CheckCodexScript"
    exit 1
}

# Preflight: Check active Codex CLI process (fail-closed on 1 and 2)
& $PythonExe -B $CheckCodexScript
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}


Write-Host "`n=== Khoi phuc cai dat goc OpenAI Codex CLI ===" -ForegroundColor Cyan

# 1. Tat tien trinh proxy
Write-Host "-> Dang tat tien trinh Proxy API..." -ForegroundColor Cyan
if (-not $SkipProxy) {
    & (Join-Path $ScriptDir "stop.ps1")
} else {
    Write-Host "-> [TEST_MODE] Bo qua tat proxy." -ForegroundColor Yellow
}

# 2. Dong bo toan bo lich su chat ve provider 'openai'
Write-Host "-> Dong bo toan bo lich su chat ve provider 'openai'..." -ForegroundColor Cyan
& $PythonExe $SyncScript openai
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Dong bo lich su chat sang 'openai' that bai! Rollback session ve 'custom' va huy bo uninstall." -ForegroundColor Red
    & $PythonExe $SyncScript custom | Out-Null
    & $PythonExe $SyncScript --verify custom | Out-Null
    exit 1
}

# 3. Xac minh toan bo lich su chat da chuyen sang 'openai'
Write-Host "-> Xac minh toan bo lich su chat sang 'openai'..." -ForegroundColor Cyan
& $PythonExe $SyncScript --verify openai
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Xac minh lich su chat that bai! Rollback session ve 'custom' va huy bo uninstall." -ForegroundColor Red
    & $PythonExe $SyncScript custom | Out-Null
    & $PythonExe $SyncScript --verify custom | Out-Null
    exit 1
}

# 4. Khoi phuc config.toml ve ban goc ban dau
Write-Host "-> Khoi phuc config.toml ban dau..." -ForegroundColor Cyan
& $PythonExe $ConfigScript restore
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Khoi phuc config.toml that bai! Rollback session ve 'custom' va huy bo uninstall." -ForegroundColor Red
    & $PythonExe $SyncScript custom | Out-Null
    & $PythonExe $SyncScript --verify custom | Out-Null
    exit 1
}

# 5. Mo khoa va xoa models_cache.json tuy chinh de Codex CLI tu tao lai cache cua OpenAI
Write-Host "-> Xu ly models_cache.json tuy chinh..." -ForegroundColor Cyan
if ($FailStep -eq "remove-cache") {
    Write-Host "`n[FAIL_INJECTION] Injected failure at remove-cache" -ForegroundColor Red
    exit 1
}
if (Test-Path $ModelsCachePath) {
    Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    Remove-Item -Path $ModelsCachePath -Force -ErrorAction SilentlyContinue
    if (Test-Path $ModelsCachePath) {
        Write-Error "Khong the xoa file models_cache.json tai $ModelsCachePath!"
        exit 1
    }
    Write-Host "-> Da xoa models_cache.json tuy chinh (Codex se tu dung cac model mac dinh cua OpenAI)." -ForegroundColor Green
}

# 6. Go bo lenh 'aic' khoi User PATH & PowerShell Profile
Write-Host "-> Go bo lenh 'aic' khoi PATH & PowerShell Profile..." -ForegroundColor Cyan
if ($UserPathFile) {
    if (Test-Path $UserPathFile) {
        $curr = Get-Content $UserPathFile -Raw
        $cleanP = ($curr.Split(';') | Where-Object { $_ -ne $BinDir -and $_ -ne "" }) -join ';'
        Set-Content -Path $UserPathFile -Value $cleanP -Encoding utf8
    }
} elseif (-not $IsTestMode) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -and $BinDir) {
        $NewPaths = $UserPath.Split(';') | Where-Object {
            $_ -ne "" -and (Resolve-Path $_ -ErrorAction SilentlyContinue).Path -ne $BinDir
        }
        $NewUserPath = $NewPaths -join ';'
        [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
        Write-Host "-> Da go $BinDir khoi User PATH." -ForegroundColor Green
    }
}

if (Test-Path $ProfilePath) {
    $ManageProfile = Join-Path $ScriptDir "scripts\manage_profile.py"
    & $PythonExe $ManageProfile --profile "$ProfilePath" --action uninstall
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Go bo block 'aic' khoi PowerShell Profile that bai."
        exit $LASTEXITCODE
    }
    Write-Host "-> Da go block 'aic' khoi PowerShell Profile." -ForegroundColor Green
}
$ProfileStateFile = Join-Path $CodexDir "aic_profile_rollback.json"
if (Test-Path $ProfileStateFile) { Remove-Item -Path $ProfileStateFile -Force -ErrorAction SilentlyContinue }


# 7. Don dep thu muc aic-backup
& $PythonExe $ConfigScript clean-backup | Out-Null

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "   DA KHOI PHUC CAI DAT GOC & GO BO 'aic' THANH CONG 100%!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Cau hinh Codex CLI da duoc khoi phuc ve provider OpenAI goc," -ForegroundColor Cyan
Write-Host "toan bo lich su chat cu & moi duoc bao toan va co the tiep tuc resume.`n" -ForegroundColor Cyan
