$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$gameUrl = "http://localhost:3300"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
Set-Location -LiteralPath $projectRoot

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "Node.js is not installed. Install Node.js 20 or newer."
}

$python = Join-Path $projectRoot "telegram_bot\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    & (Join-Path $projectRoot "telegram_bot\setup_bot.ps1")
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Telegram bot virtual environment was not created."
}

& $python -m telegram_bot init
if ($LASTEXITCODE -ne 0) { throw "Telegram bot initialization failed." }
& $python -m telegram_bot security-check
if ($LASTEXITCODE -ne 0) { throw "Telegram bot security check failed." }

$botOut = Join-Path $runtimeDir "bot.stdout.log"
$botErr = Join-Path $runtimeDir "bot.stderr.log"
$serverOut = Join-Path $runtimeDir "server.stdout.log"
$serverErr = Join-Path $runtimeDir "server.stderr.log"

$bot = Start-Process -FilePath $python `
    -ArgumentList @("-m", "telegram_bot", "run") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $botOut `
    -RedirectStandardError $botErr `
    -PassThru

$server = Start-Process -FilePath $nodeCommand.Source `
    -ArgumentList @("server.js") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $serverOut `
    -RedirectStandardError $serverErr `
    -PassThru

@{
    bot = @{ id = $bot.Id; started = $bot.StartTime.ToUniversalTime().ToString("o"); name = $bot.ProcessName }
    server = @{ id = $server.Id; started = $server.StartTime.ToUniversalTime().ToString("o"); name = $server.ProcessName }
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeDir "processes.json") -Encoding UTF8

$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$gameUrl/api/health" -TimeoutSec 2
        if ($health.status -eq "ok") { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    throw "Local game server did not become ready. Check .runtime/server.stderr.log."
}

$chrome = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)'
$edge = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" -ErrorAction SilentlyContinue).'(default)'
if ($chrome -and (Test-Path -LiteralPath $chrome)) {
    Start-Process -FilePath $chrome -ArgumentList @("--kiosk", $gameUrl)
} elseif ($edge -and (Test-Path -LiteralPath $edge)) {
    Start-Process -FilePath $edge -ArgumentList @("--kiosk", $gameUrl)
} else {
    Start-Process $gameUrl
}

Write-Host "Flexy Way is running at $gameUrl"
Write-Host "Phones connect through the Render relay shown in the QR code."
