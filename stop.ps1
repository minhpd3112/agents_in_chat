# Stop CLIProxyAPI
$Stopped = $false
$Processes = Get-Process | Where-Object { $_.ProcessName -like "*cli-proxy*" }
foreach ($p in $Processes) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    $Stopped = $true
}

$conn = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    $Stopped = $true
}

if ($Stopped) {
    Write-Host "-> [OFFLINE] Da tat tien trinh CLIProxyAPI thanh cong." -ForegroundColor Green
} else {
    Write-Host "-> CLIProxyAPI hien khong chay." -ForegroundColor Yellow
}

# [SAFETY] Auto-Backup: snapshot trang thai token moi nhat sau khi proxy da dung han.
$BackupScript = Join-Path $PSScriptRoot "scripts\backup_auths.py"
if (Test-Path $BackupScript) {
    $PythonBin = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }
    & $PythonBin -B $BackupScript backup
}
