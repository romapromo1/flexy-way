$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Изолированное окружение не найдено. Сначала запустите telegram_bot/setup_bot.ps1"
}

& $python -m telegram_bot init
& $python -m telegram_bot security-check
& $python -m telegram_bot run
