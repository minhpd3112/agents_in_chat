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

# Preflight: Validate auths isolation in test mode
if ($IsTestMode) {
    if (-not $env:AIC_AUTHS_DIR -or -not $env:AIC_AUTHS_BACKUP_DIR) {
        Write-Error "AIC_TEST_MODE=1 requires AIC_AUTHS_DIR and AIC_AUTHS_BACKUP_DIR to be set"
        exit 1
    }
    $AuthsDir = $env:AIC_AUTHS_DIR
    $BackupDir = $env:AIC_AUTHS_BACKUP_DIR
} else {
    $AuthsDir = Join-Path $ScriptDir "auths"
    $BackupDir = Join-Path $ScriptDir "auths_backup"
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
    if ($State_ConfigModified) {
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
    if ($State_ProfileAdded) {
        $ProfileStateFile = Join-Path $CodexDir "aic_profile_rollback.json"
        & $PythonExe (Join-Path $ScriptDir "scripts\manage_profile.py") --profile "$ProfilePath" --action rollback --state-file "$ProfileStateFile" | Out-Null
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
    $VersionFile = Join-Path $ScriptDir "VERSION"
    $AicVersion = if (Test-Path $VersionFile) { (Get-Content $VersionFile -Raw).Trim() } else { "" }
    $VerLabel = if ($AicVersion) { " v$AicVersion" } else { "" }
    Write-Host "`n=== Kiem tra moi truong agents_in_chat$VerLabel ===" -ForegroundColor Cyan

    # Proxy binary
    $ProxyExe = Join-Path $ScriptDir "cli-proxy-api.exe"
    if (-not $IsTestMode -and -not (Test-Path $ProxyExe) -and -not $SkipDownload) {
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
    if (-not $IsTestMode -and -not (Test-Path $ConfigFile) -and (Test-Path $ConfigExample)) {
        Copy-Item -Path $ConfigExample -Destination $ConfigFile -Force
        Write-Host "-> Da khoi tao config.yaml tu config.example.yaml." -ForegroundColor Green
    }

    # auths dir
    if (-not (Test-Path $AuthsDir)) {
        New-Item -ItemType Directory -Path $AuthsDir -Force | Out-Null
    }

    # [SAFETY] Khoi tao kho sao luu token & chup snapshot ban dau
    Write-Host "`n=== Khoi tao Atomic Auto-Backup cho thu muc auths/ ===" -ForegroundColor Cyan
    $BackupScript = if ($IsTestMode -and $env:AIC_BACKUP_SCRIPT) { $env:AIC_BACKUP_SCRIPT } else { Join-Path $ScriptDir "scripts\backup_auths.py" }
    if (-not (Test-Path $BackupScript)) {
        [Console]::Error.WriteLine("[ERROR] Thieu helper bat buoc tai $BackupScript")
        exit 1
    }
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    & $PythonExe -B $BackupScript backup
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("[ERROR] Khoi tao sao luu auths that bai. Dinh chi cai dat.")
        exit 1
    }

    # 2. Backup & Configure TOML
    Write-Host "`n=== Backup & Cau hinh ~/.codex/config.toml ===" -ForegroundColor Cyan
    $State_ConfigModified = $true
    & $PythonExe $ConfigScript custom
    if ($LASTEXITCODE -ne 0) {
        throw "Cau hinh config.toml that bai."
    }
    $State_ConfigModified = $true

    # 3. Models cache template & Default Compat Auths
    Write-Host "`n=== Cau hinh & Khoa READ-ONLY ~/.codex/models_cache.json ===" -ForegroundColor Cyan
    $TemplateJson = Join-Path $ScriptDir "docs\models_cache_template.json"
    if (-not (Test-Path $TemplateJson)) {
        throw "Khong tim thay template tai $TemplateJson!"
    }
    if (Test-Path $ModelsCachePath) {
        Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    }

    # Tu dong nhan dien phien ban Codex CLI hien tai de dong bo client_version, mac dinh lay tu template
    $TemplateData = Get-Content $TemplateJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $CodexVer = if ($TemplateData.client_version) { $TemplateData.client_version } else { "0.153.0" }
    try {
        $VerOut = & codex --version 2>$null
        if ($VerOut -match '(\d+\.\d+\.\d+)') {
            $CodexVer = $Matches[1]
        }
    } catch {}
    $TemplateData.client_version = $CodexVer
    $JsonContent = $TemplateData | ConvertTo-Json -Depth 30
    [IO.File]::WriteAllText($ModelsCachePath, $JsonContent, (New-Object System.Text.UTF8Encoding($false)))
    Set-ItemProperty -Path $ModelsCachePath -Name IsReadOnly -Value $true
    $State_CacheModified = $true

    $ZenAuthPath = Join-Path $AuthsDir "openai-compatible-opencode-zen.json"
    if (-not (Test-Path $ZenAuthPath)) {
        $ZenAuthJson = @'
{
  "type": "openai-compatible",
  "provider": "openai-compatible-opencode-zen",
  "name": "opencode-zen",
  "url": "https://opencode.ai/zen/v1",
  "base_url": "https://opencode.ai/zen/v1",
  "key": "public",
  "api_key": "public",
  "models": [
    "x-preview-f-free",
    "ox-alpha"
  ]
}
'@
        Set-Content -Path $ZenAuthPath -Value $ZenAuthJson -Encoding utf8
    }

    $ModelCount = if ($TemplateData.models) { $TemplateData.models.Count } else { 0 }
    if ($ModelCount -gt 0) {
        Write-Host "-> Da nap $ModelCount dinh nghia model & KHOA READ-ONLY cache menu cho Codex CLI." -ForegroundColor Green
    } else {
        Write-Host "-> Da nap danh muc model & KHOA READ-ONLY cache menu cho Codex CLI." -ForegroundColor Green
    }

    # 4. Sync sessions & Verify
    Write-Host "`n=== Dong bo & Xac minh lich su chat sang 'custom' ===" -ForegroundColor Cyan
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
    Write-Host "`n=== Dang ky lenh toan cuc 'aic' ===" -ForegroundColor Cyan
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
    $SyncHelperPy = Join-Path $ScriptDir "scripts\sync_client_version.py"
    $ProfileBlock = @"
# >>> AIC >>>
function global:aic { & "$PythonExe" "$AicPy" `$args }
function global:codex {
    `$syncHelper = "$SyncHelperPy"
    if (Test-Path `$syncHelper) {
        try { & "$PythonExe" `$syncHelper } catch {}
    }
    `$app = Get-Command -Name "codex.exe" -CommandType Application -ErrorAction SilentlyContinue | Where-Object { `$_.Source -ne `$MyInvocation.MyCommand.Definition } | Select-Object -First 1
    if (`$app) {
        & `$app.Source @args
    } else {
        Write-Error "codex.exe not found in PATH."
    }
}
# <<< AIC <<<
"@
    $ProfileStateFile = Join-Path $CodexDir "aic_profile_rollback.json"
    $ManageProfile = Join-Path $ScriptDir "scripts\manage_profile.py"
    $ProfileBlockTemp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($ProfileBlockTemp, $ProfileBlock, [System.Text.UTF8Encoding]::new($false))
        & $PythonExe $ManageProfile --profile "$ProfilePath" --action install --block-file "$ProfileBlockTemp" --state-file "$ProfileStateFile"
        if ($LASTEXITCODE -ne 0) {
            throw "Dang ky profile that bai."
        }
    }
    finally {
        if (Test-Path $ProfileBlockTemp) {
            Remove-Item $ProfileBlockTemp -Force -ErrorAction SilentlyContinue
        }
    }
    $State_ProfileAdded = $true
    Write-Host "-> Da dang ky ham 'aic' & 'codex' wrapper vao PowerShell Profile." -ForegroundColor Green


    # 6. Start proxy service
    Write-Host "`n=== Khoi dong CLIProxyAPI Service ===" -ForegroundColor Cyan
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

    # Clean up profile transaction state on install success
    if (Test-Path $ProfileStateFile) {
        Remove-Item -Path $ProfileStateFile -Force -ErrorAction SilentlyContinue
    }

    Write-Host "`n🎉 AIC installed successfully! Run 'aic' or 'codex' to get started.`n" -ForegroundColor Green
}
catch {
    Invoke-Rollback $_.Exception.Message
}
