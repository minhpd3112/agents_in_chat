# ==============================================================================
#  agents_in_chat: One-Click Installer for Windows (PowerShell)
#  Tu dong cau hinh Codex CLI, dang ky lenh toan cuc 'aic' & Khoi dong Proxy
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }

# 0. Test Mode & Environment Isolation
$IsTestMode = ($env:AIC_TEST_MODE -eq "1")
$CodexDir = if ($IsTestMode -and $env:AIC_CODEX_DIR) { (Resolve-Path $env:AIC_CODEX_DIR).Path } else { Join-Path $env:USERPROFILE ".codex" }
$ProfilePath = if ($IsTestMode -and $env:AIC_PROFILE_PATH) { $env:AIC_PROFILE_PATH } else { $PROFILE }
$UserPathFile = if ($IsTestMode -and $env:AIC_USER_PATH_FILE) { $env:AIC_USER_PATH_FILE } else { $null }
$SkipDownload = ($IsTestMode -and $env:AIC_SKIP_DOWNLOAD -eq "1")
$SkipProxy = ($IsTestMode -and $env:AIC_SKIP_PROXY -eq "1")
$FailStep = if ($IsTestMode) { $env:AIC_FAIL_STEP } else { $null }

# 1. Tim kiem Python executable
$PythonExe = ""
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $PythonExe = "python3"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} else {
    Write-Error "Khong tim thay Python! Vui long cai dat Python (>=3.8) truoc khi chay install."
    exit 1
}

$ModelsCachePath = Join-Path $CodexDir "models_cache.json"
$ConfigScript = Join-Path $ScriptDir "scripts\configure_codex_toml.py"
$SyncScript = Join-Path $ScriptDir "scripts\sync_sessions.py"
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

# Transaction state tracking for full rollback
$State_ConfigModified = $false
$State_CacheModified = $false
$State_SyncExecuted = $false
$State_PathAdded = $false
$State_ProfileAdded = $false

function Invoke-Rollback {
    param([string]$Reason)
    Write-Host "`n[ROLLBACK] Phat hien su co: $Reason" -ForegroundColor Red
    Write-Host "-> Dang hoan tac toan dien he thong ve trang thai ban dau..." -ForegroundColor Yellow

    # 1. Restore config.toml
    if ($State_ConfigModified -or (Test-Path (Join-Path $CodexDir "aic-backup"))) {
        & $PythonExe $ConfigScript restore | Out-Null
    }
    # 2. Rollback sync sessions to openai
    if ($State_SyncExecuted) {
        & $PythonExe $SyncScript openai | Out-Null
        & $PythonExe $SyncScript --verify openai | Out-Null
    }
    # 3. Unlock & remove custom cache if modified
    if ($State_CacheModified -and (Test-Path $ModelsCachePath)) {
        Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
        Remove-Item -Path $ModelsCachePath -Force -ErrorAction SilentlyContinue
    }
    # 4. Remove profile block if added
    if ($State_ProfileAdded -and (Test-Path $ProfilePath)) {
        $pContent = Get-Content $ProfilePath -Raw
        $cleaned = $pContent -replace '(?s)\r?\n?# >>> AIC >>>.*?# <<< AIC <<<', ''
        Set-Content -Path $ProfilePath -Value $cleaned.Trim() -Encoding utf8
    }
    # 5. Remove PATH entry if added
    if ($State_PathAdded) {
        if ($UserPathFile -and (Test-Path $UserPathFile)) {
            $curr = Get-Content $UserPathFile -Raw
            $newP = ($curr.Split(';') | Where-Object { $_ -ne $BinDir -and $_ -ne "" }) -join ';'
            Set-Content -Path $UserPathFile -Value $newP -Encoding utf8
        } elseif (-not $IsTestMode) {
            $uPath = [Environment]::GetEnvironmentVariable("Path", "User")
            if ($uPath) {
                $newP = ($uPath.Split(';') | Where-Object { $_ -ne $BinDir -and $_ -ne "" }) -join ';'
                [Environment]::SetEnvironmentVariable("Path", $newP, "User")
            }
        }
    }
    Write-Host "-> Da hoan tac an toan. Vui long kiem tra loi tren va chay lai install.ps1.`n" -ForegroundColor Yellow
    exit 1
}

try {
    Write-Host "`n=== [1/6] Kiem tra moi truong agents_in_chat ===" -ForegroundColor Cyan

    # Proxy binary
    $ProxyExe = Join-Path $ScriptDir "cli-proxy-api.exe"
    if (-not (Test-Path $ProxyExe) -and -not $SkipDownload) {
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

    # config.yaml
    $ConfigFile = Join-Path $ScriptDir "config.yaml"
    $ConfigExample = Join-Path $ScriptDir "config.example.yaml"
    if (-not (Test-Path $ConfigFile) -and (Test-Path $ConfigExample)) {
        Copy-Item -Path $ConfigExample -Destination $ConfigFile -Force
        Write-Host "-> Da khoi tao config.yaml tu config.example.yaml." -ForegroundColor Green
    }

    # auths dir
    $AuthsDir = Join-Path $ScriptDir "auths"
    if (-not (Test-Path $AuthsDir)) {
        New-Item -ItemType Directory -Path $AuthsDir | Out-Null
    }

    # 2. Backup & Configure TOML
    Write-Host "`n=== [2/6] Backup & Cau hinh ~/.codex/config.toml ===" -ForegroundColor Cyan
    & $PythonExe $ConfigScript custom
    if ($LASTEXITCODE -ne 0) {
        throw "Cau hinh config.toml that bai."
    }
    $State_ConfigModified = $true

    # 3. Models cache template
    Write-Host "`n=== [3/6] Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ===" -ForegroundColor Cyan
    $TemplateJson = Join-Path $ScriptDir "docs\models_cache_template.json"
    if (-not (Test-Path $TemplateJson)) {
        throw "Khong tim thay template tai $TemplateJson!"
    }
    if (Test-Path $ModelsCachePath) {
        Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    }
    Copy-Item -Path $TemplateJson -Destination $ModelsCachePath -Force
    Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $true
    $State_CacheModified = $true
    Write-Host "-> Da nap 6 models & KHOA READ-ONLY thanh cong vao models_cache.json." -ForegroundColor Green

    # 4. Sync sessions & Verify
    Write-Host "`n=== [4/6] Dong bo & Xac minh lich su chat sang 'custom' ===" -ForegroundColor Cyan
    & $PythonExe $SyncScript custom
    if ($LASTEXITCODE -ne 0) {
        throw "Dong bo lich su session sang 'custom' that bai."
    }
    $State_SyncExecuted = $true

    & $PythonExe $SyncScript --verify custom
    if ($LASTEXITCODE -ne 0) {
        throw "Xac minh lich su session sau dong bo that bai."
    }

    # 5. Register PATH & Profile
    Write-Host "`n=== [5/6] Dang ky lenh toan cuc 'aic' ===" -ForegroundColor Cyan
    if ($UserPathFile) {
        $curr = if (Test-Path $UserPathFile) { Get-Content $UserPathFile -Raw } else { "" }
        $cleanP = if ($curr) { ($curr.Split(';') | Where-Object { $_ -ne "" }) } else { @() }
        if ($cleanP -notcontains $BinDir) {
            $newP = if ($curr) { "$curr;$BinDir" } else { $BinDir }
            Set-Content -Path $UserPathFile -Value $newP -Encoding utf8
            $State_PathAdded = $true
        }
    } elseif (-not $IsTestMode) {
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $CleanPaths = if ($UserPath) { ($UserPath.Split(';') | Where-Object { $_ -ne "" }) } else { @() }
        if ($CleanPaths -notcontains $BinDir) {
            $NewUserPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
            [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
            $State_PathAdded = $true
            Write-Host "-> Da them $BinDir vao User PATH." -ForegroundColor Green
        }
        $env:Path = "$env:Path;$BinDir"
    }

    # PowerShell profile
    $ProfileDir = Split-Path -Parent $ProfilePath
    if (-not (Test-Path $ProfileDir)) { New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null }
    $AicPy = Join-Path $BinDir "aic.py"
    $ProfileBlock = @"
# >>> AIC >>>
function global:aic { python "$AicPy" `$args }
# <<< AIC <<<
"@
    if (Test-Path $ProfilePath) {
        $pContent = Get-Content $ProfilePath -Raw
        if ($pContent -notmatch '# >>> AIC >>>' -and $pContent -notmatch 'function global:aic') {
            Add-Content -Path $ProfilePath -Value "`n$ProfileBlock"
            $State_ProfileAdded = $true
            Write-Host "-> Da dang ky ham 'aic' vao PowerShell Profile." -ForegroundColor Green
        }
    } else {
        Set-Content -Path $ProfilePath -Value $ProfileBlock -Encoding utf8
        $State_ProfileAdded = $true
        Write-Host "-> Da khoi tao PowerShell Profile voi ham 'aic'." -ForegroundColor Green
    }

    # 6. Start proxy service
    Write-Host "`n=== [6/6] Khoi dong CLIProxyAPI Service ===" -ForegroundColor Cyan
    if ($FailStep -eq "start") {
        throw "Simulation of start failure at step 6"
    }
    if (-not $SkipProxy) {
        & (Join-Path $ScriptDir "stop.ps1") | Out-Null
        & (Join-Path $ScriptDir "start.ps1")
        if ($LASTEXITCODE -ne 0) {
            throw "Khoi dong proxy that bai."
        }
    } else {
        Write-Host "-> [TEST_MODE] Bo qua khoi dong proxy." -ForegroundColor Yellow
    }

    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host "   CAI DAT & DANG KY LENH TOAN CUC 'aic' THANH CONG 100%!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "Bay gio ban co the mo Terminal tai BAT KY THU MUC NAO va dung:"
    Write-Host "  - Bat dau chat:      codex"
    Write-Host "  - Kiem tra he thong: aic status"
    Write-Host "  - Chay kiem thu:     aic test"
    Write-Host "  - Tat / Bat proxy:   aic stop  /  aic start"
    Write-Host "  - Khoi phuc goc:     aic uninstall`n"
}
catch {
    Invoke-Rollback $_.Exception.Message
}
