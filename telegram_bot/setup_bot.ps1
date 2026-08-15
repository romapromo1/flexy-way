$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$venv = Join-Path $PSScriptRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv $venv
}

& $python -m pip install --requirement (Join-Path $PSScriptRoot "requirements.txt")
& $python -m telegram_bot init
& $python -m telegram_bot security-check
& $python -m unittest discover -s telegram_bot/tests -v
