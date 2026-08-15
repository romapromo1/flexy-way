[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = $PSScriptRoot
$DesktopFolder = [System.Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopFolder "Flexy Way 3D Game.lnk"
$TargetBat = Join-Path $Root "START_FLEXY_WAY.bat"

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($ShortcutPath)
$s.TargetPath = $TargetBat
$s.WorkingDirectory = $Root
$s.Description = "Запуск игрового комплекса Flexy Way и Telegram-бота"
$s.Save()

if (Test-Path $ShortcutPath) {
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "✅ Ярлык «Flexy Way 3D Game» успешно создан на Рабочем столе!" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
} else {
    Write-Host "❌ Ошибка создания ярлыка." -ForegroundColor Red
}
Start-Sleep -Seconds 3
