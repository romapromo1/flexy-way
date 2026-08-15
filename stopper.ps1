[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Flexy Way - Stopper"
$Root = $PSScriptRoot

Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "          🛑 ОСТАНОВКА КОМПЛЕКСА FLEXY WAY 🛑                  " -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host ""

Write-Host "Остановка игрового сервера Node.js..." -ForegroundColor Gray
Get-Process -Name "node" -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmd -like "*server.js*"
    } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "Остановка Telegram-бота..." -ForegroundColor Gray
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmd -like "*telegram_bot*"
    } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue

$LockFile = Join-Path $Root "telegram_bot\data\flexy_way_bot.sqlite3.run.lock"
if (Test-Path $LockFile) {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "   ✅ Все службы Flexy Way успешно остановлены!                 " -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host ""
Start-Sleep -Seconds 3
