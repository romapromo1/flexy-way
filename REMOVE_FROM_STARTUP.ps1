[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "FlexyWay_AutoStart.lnk"

if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force -ErrorAction SilentlyContinue
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host "✅ Автозапуск Flexy Way при включении ПК успешно отключен." -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Yellow
} else {
    Write-Host "ℹ️ Автозапуск не был включен." -ForegroundColor Gray
}
Write-Host ""
Start-Sleep -Seconds 3
