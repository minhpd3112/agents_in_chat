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
