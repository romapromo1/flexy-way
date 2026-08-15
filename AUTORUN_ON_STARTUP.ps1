[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = $PSScriptRoot
$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "FlexyWay_AutoStart.lnk"
$TargetBat = Join-Path $Root "START_FLEXY_WAY.bat"

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($ShortcutPath)
$s.TargetPath = $TargetBat
$s.WorkingDirectory = $Root
$s.Description = "Автозапуск Flexy Way при включении Windows"
$s.Save()

if (Test-Path $ShortcutPath) {
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "✅ АВТОЗАПУСК УСПЕШНО ВКЛЮЧЕН!" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Теперь при каждой перезагрузке или включении ПК:" -ForegroundColor White
    Write-Host " 1. Автоматически запустится Telegram-бот" -ForegroundColor Cyan
    Write-Host " 2. Автоматически запустится сервер игры Node.js" -ForegroundColor Cyan
    Write-Host " 3. Автоматически откроется игра на весь экран" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Для отключения запустите REMOVE_FROM_STARTUP.bat" -ForegroundColor Yellow
} else {
    Write-Host "❌ Ошибка включения автозапуска." -ForegroundColor Red
}
Write-Host ""
Start-Sleep -Seconds 4
