[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Flexy Way - Unified Launcher"
$Root = $PSScriptRoot

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "          🚀 ЗАПУСК ИГРОВОГО КОМПЛЕКСА FLEXY WAY 🚀             " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Проверка Node.js
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "[ОШИБКА] Node.js не установлен в системе!" -ForegroundColor Red
    Write-Host "Установите Node.js с официального сайта: https://nodejs.org/" -ForegroundColor Yellow
    Read-Host "Нажмите Enter для выхода..."
    exit 1
}

# 2. Проверка виртуального окружения Python
$PythonExe = Join-Path $Root "telegram_bot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "[1/3] Настройка виртуального окружения Telegram-бота..." -ForegroundColor Yellow
    $SetupScript = Join-Path $Root "telegram_bot\setup_bot.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SetupScript
}

# 3. Запуск Telegram-бота в фоне
Write-Host "[1/3] 🤖 Запуск Telegram-бота @flexy_way_prize_bot..." -ForegroundColor Green
Start-Process -FilePath $PythonExe -ArgumentList "-m", "telegram_bot", "run" -WorkingDirectory $Root -WindowStyle Minimized

# 4. Запуск игрового сервера Node.js в фоне
Write-Host "[2/3] 🎮 Запуск игрового сервера Node.js и туннелей..." -ForegroundColor Green
Start-Process -FilePath "node" -ArgumentList "server.js" -WorkingDirectory $Root -WindowStyle Minimized

# Пауза для инициализации серверов
Start-Sleep -Seconds 3

# 5. Запуск игры на полный экран
Write-Host "[3/3] 🖥️ Открытие игрового экрана на 86-дюймовой панели..." -ForegroundColor Green
$GameUrl = "http://localhost:3300"
$Chrome = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)'
$Edge = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" -ErrorAction SilentlyContinue).'(default)'

if ($Chrome -and (Test-Path $Chrome)) {
    Start-Process -FilePath $Chrome -ArgumentList "--start-fullscreen", $GameUrl
} elseif ($Edge -and (Test-Path $Edge)) {
    Start-Process -FilePath $Edge -ArgumentList "--start-fullscreen", $GameUrl
} else {
    Start-Process $GameUrl
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "   ✅ ВСЕ КОМПОНЕНТЫ УСПЕШНО ЗАПУЩЕНЫ И РАБОТАЮТ!               " -ForegroundColor Green
Write-Host ""
Write-Host "   • Игровой экран (ПК):   http://localhost:3300" -ForegroundColor White
Write-Host "   • Мобильный пульт:      http://localhost:3300/controller" -ForegroundColor White
Write-Host "   • Telegram-бот:         @flexy_way_prize_bot (активен)" -ForegroundColor White
Write-Host ""
Write-Host "   Чтобы остановить комплекс, запустите STOP_FLEXY_WAY.bat" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Start-Sleep -Seconds 5
