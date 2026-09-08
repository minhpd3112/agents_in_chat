# Stop CLIProxyAPI
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }
$PythonBin = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }
$ProxyManager = Join-Path $ScriptDir "scripts\proxy_manager.py"
& $PythonBin -B $ProxyManager stop
exit $LASTEXITCODE
