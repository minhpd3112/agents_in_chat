# Start CLIProxyAPI completely hidden & detached
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }
$ProxyExe = Join-Path $ScriptDir "cli-proxy-api.exe"

$conn = Test-NetConnection -ComputerName "127.0.0.1" -Port 8080 -WarningAction SilentlyContinue
if ($conn.TcpTestSucceeded) {
    Write-Host "-> [ONLINE] CLIProxyAPI dang hoat dong san sang." -ForegroundColor Yellow
    return
}

$ConfigFile = Join-Path $ScriptDir "config.yaml"
$startup = New-CimInstance -ClassName Win32_ProcessStartup -Property @{ ShowWindow = [UInt16]0 } -ClientOnly
$proc = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "`"$ProxyExe`" -config `"$ConfigFile`""; CurrentDirectory = "$ScriptDir"; ProcessStartupInformation = $startup }
Start-Sleep -Seconds 2

$PortCheck = Test-NetConnection -ComputerName "127.0.0.1" -Port 8080 -WarningAction SilentlyContinue
if ($PortCheck.TcpTestSucceeded) {
    Write-Host "-> [ONLINE] CLIProxyAPI da khoi dong chay ngam thanh cong." -ForegroundColor Green
    return
} else {
    Write-Host "-> [WARNING] Da chay binary nhung dich vu proxy chua phan hoi." -ForegroundColor Yellow
    exit 1
}
