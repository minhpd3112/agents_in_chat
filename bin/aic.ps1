param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ArgsList
)

$BinDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $BinDir

switch ($Command.ToLower()) {
    "start" {
        & "$RootDir\start.ps1"
    }
    "stop" {
        & "$RootDir\stop.ps1"
    }
    "restart" {
        & "$RootDir\stop.ps1"
        Start-Sleep -Seconds 1
        & "$RootDir\start.ps1"
    }
    "uninstall" {
        & "$RootDir\uninstall.ps1"
    }
    "test" {
        python "$RootDir\tests\run_tests.py"
    }
    "status" {
        Write-Host "`n=== AGENTS IN CHAT (AIC) SYSTEM STATUS ===" -ForegroundColor Cyan
        $conn = Test-NetConnection -ComputerName "127.0.0.1" -Port 8080 -WarningAction SilentlyContinue
        if ($conn.TcpTestSucceeded) {
            Write-Host "-> Proxy Service (127.0.0.1:8080): ONLINE [200 OK]" -ForegroundColor Green
        } else {
            Write-Host "-> Proxy Service (127.0.0.1:8080): OFFLINE" -ForegroundColor Red
        }
        
        $configPath = "$env:USERPROFILE\.codex\config.toml"
        if (Test-Path $configPath) {
            $conf = Get-Content $configPath -Raw
            if ($conf -match 'model_provider\s*=\s*"custom"') {
                Write-Host "-> Active Model Provider: custom (Agents Quota Pool)" -ForegroundColor Yellow
            } else {
                Write-Host "-> Active Model Provider: openai (Vanilla OpenAI)" -ForegroundColor Gray
            }
        }
        
        $cachePath = "$env:USERPROFILE\.codex\models_cache.json"
        if (Test-Path $cachePath) {
            $ro = (Get-Item $cachePath).IsReadOnly
            if ($ro) {
                Write-Host "-> Models Cache Lock (Read-Only): LOCKED (+r)" -ForegroundColor Green
            } else {
                Write-Host "-> Models Cache Lock (Read-Only): UNLOCKED" -ForegroundColor Yellow
            }
        }
        Write-Host ""
    }
        "login_agy" {
        python "$RootDir\bin\aic.py" login_agy
    }
    "login_codex" {
        $mode = if ($ArgsList -and $ArgsList.Count -gt 0) { $ArgsList[0] } else { "" }
        python "$RootDir\bin\aic.py" login_codex $mode
    }
    "login" {
        $target = "anti"
        if ($ArgsList -and $ArgsList.Count -gt 0) { $target = $ArgsList[0] }
        python "$RootDir\bin\aic.py" login $target
    }
    "sync" {
        $target = "custom"
        if ($ArgsList -and $ArgsList.Count -gt 0) { $target = $ArgsList[0] }
        python "$RootDir\scripts\sync_sessions.py" $target
    }
    default {
        Write-Host "`n============================================================" -ForegroundColor Cyan
        Write-Host "             AGENTS IN CHAT (AIC) CLI MANAGER              " -ForegroundColor Cyan
        Write-Host "============================================================" -ForegroundColor Cyan
        Write-Host "Su dung: aic <lenh>`n"
        Write-Host "Cac lenh kha dung:"
        Write-Host "  aic start       - Khoi dong Proxy API chay ngam tren cong 8080" -ForegroundColor Green
        Write-Host "  aic stop        - Tat Proxy API va giai phong tai nguyen" -ForegroundColor Yellow
        Write-Host "  aic restart     - Khoi dong lai Proxy API" -ForegroundColor Green
        Write-Host "  aic status      - Kiem tra tinh trang ket noi, cong 8080, model" -ForegroundColor Cyan
        Write-Host "  aic test        - Chay bo kiem thu tu dong 7/7 test suites" -ForegroundColor Magenta
        Write-Host "  aic uninstall   - Factory Reset 100% ve nguyen ban OpenAI Codex" -ForegroundColor Red
        Write-Host "  aic sync        - Dong bo lich su chat giua custom va openai" -ForegroundColor Gray
        Write-Host "`nDanh sach 6 model ho tro trong /model:"
        Write-Host "  1. gemini-3.7-flash            (Thinking, Function Calling)"
        Write-Host "  2. claude-sonnet-4.6-thinking  (Thinking, Tool Calling)"
        Write-Host "  3. claude-opus-4.6-thinking    (Ultra Thinking)"
        Write-Host "  4. gpt-5.6-sol                 (Flagship Frontier)"
        Write-Host "  5. gpt-5.6-terra               (Balanced Agentic)"
        Write-Host "  6. gpt-5.6-luna                (Fast & Affordable)"
        Write-Host "============================================================`n"
    }
}
