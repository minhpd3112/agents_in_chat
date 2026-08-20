# ==============================================================================
#  agents_in_chat: One-Click Installer for Windows (PowerShell)
#  Tu dong cau hinh Codex CLI, dang ky lenh toan cuc 'aic' & Khoi dong Proxy
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }

Write-Host "`n=== [1/6] Kiem tra moi truong agents_in_chat ===" -ForegroundColor Cyan

# 1. Kiem tra binary proxy (Tu dong tai neu chua co)
$ProxyExe = Join-Path $ScriptDir "cli-proxy-api.exe"
if (-not (Test-Path $ProxyExe)) {
    Write-Host "-> Khong tim thay cli-proxy-api.exe tai thu muc goc." -ForegroundColor Yellow
    Write-Host "-> Dang tai CLIProxyAPI ban moi nhat tu GitHub Releases..." -ForegroundColor Cyan
    $ZipPath = Join-Path $ScriptDir "CLIProxyAPI.zip"
    $DownloadUrl = "https://github.com/router-for-me/CLIProxyAPI/releases/latest/download/CLIProxyAPI_Windows_x86_64.zip"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $ScriptDir -Force
        Remove-Item -Path $ZipPath -Force -ErrorAction SilentlyContinue
        Write-Host "-> Da tai va giai nen cli-proxy-api.exe thanh cong!" -ForegroundColor Green
    } catch {
        Write-Warning "Khong the tu dong tai binary ($($_.Exception.Message)). Vui long tai thu cong tu https://github.com/router-for-me/CLIProxyAPI/releases va dat vao $ScriptDir"
        if (-not (Test-Path $ProxyExe)) { exit 1 }
    }
} else {
    Write-Host "-> Phat hien cli-proxy-api.exe san sang tai thu muc goc." -ForegroundColor Green
}


# 2. Kiem tra file config.yaml
$ConfigFile = Join-Path $ScriptDir "config.yaml"
$ConfigExample = Join-Path $ScriptDir "config.example.yaml"
if (-not (Test-Path $ConfigFile) -and (Test-Path $ConfigExample)) {
    Copy-Item -Path $ConfigExample -Destination $ConfigFile -Force
    Write-Host "-> Da khoi tao config.yaml tu config.example.yaml." -ForegroundColor Green
}

# 3. Kiem tra thu muc auths
$AuthsDir = Join-Path $ScriptDir "auths"
if (-not (Test-Path $AuthsDir)) {
    New-Item -ItemType Directory -Path $AuthsDir | Out-Null
    Write-Host "-> Da tao thu muc auths/ de chua file token OAuth." -ForegroundColor Yellow
}
$AuthFiles = Get-ChildItem -Path $AuthsDir -Filter "*.json" -ErrorAction SilentlyContinue
Write-Host "-> Phat hien $($AuthFiles.Count) tai khoan OAuth trong auths/" -ForegroundColor Green

# 3. Cau hinh ~/.codex/config.toml qua python helper
Write-Host "`n=== [2/6] Cau hinh ~/.codex/config.toml ===" -ForegroundColor Cyan
$ConfigScript = Join-Path $ScriptDir "scripts\configure_codex_toml.py"
if (Test-Path $ConfigScript) {
    python $ConfigScript custom
}

# 4. Nap template chuan va KHOA READ-ONLY models_cache.json
Write-Host "`n=== [3/6] Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ===" -ForegroundColor Cyan
$CodexDir = Join-Path $env:USERPROFILE ".codex"
$ModelsCachePath = Join-Path $CodexDir "models_cache.json"
$TemplateJson = Join-Path $ScriptDir "docs\models_cache_template.json"

if (-not (Test-Path $TemplateJson)) {
    Write-Error "Khong tim thay template tai $TemplateJson!"
    exit 1
}

if (Test-Path $ModelsCachePath) {
    Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
}
Copy-Item -Path $TemplateJson -Destination $ModelsCachePath -Force
Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $true
Write-Host "-> Da nap 6 models & KHOA READ-ONLY thanh cong vao models_cache.json." -ForegroundColor Green

# 5. Dong bo toan bo lich su chat sang provider custom
Write-Host "`n=== [4/6] Dong bo toan bo lich su chat sang provider 'custom' ===" -ForegroundColor Cyan
$SyncScript = Join-Path $ScriptDir "scripts\sync_sessions.py"
if (Test-Path $SyncScript) {
    python $SyncScript custom
}

# 6. Dang ky lenh toan cuc 'aic' vao PATH he thong & PowerShell Profile
Write-Host "`n=== [5/6] Dang ky lenh toan cuc 'aic' ===" -ForegroundColor Cyan
$BinDir = Join-Path $ScriptDir "bin"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$BinDir*") {
    $NewUserPath = "$UserPath;$BinDir"
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    Write-Host "-> Da them $BinDir vao User PATH." -ForegroundColor Green
}
$env:Path = "$env:Path;$BinDir"

# Dang ky vao PowerShell $PROFILE
$ProfileDir = Split-Path -Parent $PROFILE
if (-not (Test-Path $ProfileDir)) { New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null }
$AicPy = Join-Path $BinDir "aic.py"
$ProfileLine = "function global:aic { python `"$AicPy`" `$args }"
if (Test-Path $PROFILE) {
    $pContent = Get-Content $PROFILE -Raw
    if ($pContent -notmatch 'function global:aic') {
        Add-Content -Path $PROFILE -Value "`n$ProfileLine"
        Write-Host "-> Da dang ky ham 'aic' vao PowerShell Profile." -ForegroundColor Green
    }
} else {
    Set-Content -Path $PROFILE -Value $ProfileLine -Encoding utf8
    Write-Host "-> Da khoi tao PowerShell Profile voi ham 'aic'." -ForegroundColor Green
}


# 7. Khoi dong Proxy Service qua WMI Detached
Write-Host "`n=== [6/6] Khoi dong CLIProxyAPI Service ===" -ForegroundColor Cyan
& (Join-Path $ScriptDir "stop.ps1") | Out-Null
& (Join-Path $ScriptDir "start.ps1")

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "   CAI DAT & DANG KY LENH TOAN CUC 'aic' THANH CONG 100%!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Bay gio ban co the mo Terminal tai BAT KY THU MUC NAO va dung:"
Write-Host "  - Bat dau chat:      codex"
Write-Host "  - Kiem tra he thong: aic status"
Write-Host "  - Chay kiem thu:     aic test"
Write-Host "  - Tat / Bat proxy:   aic stop  /  aic start"
Write-Host "  - Khoi phuc goc:     aic uninstall`n"
