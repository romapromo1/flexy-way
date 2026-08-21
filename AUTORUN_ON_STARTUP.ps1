$ErrorActionPreference = "Stop"
$shell = New-Object -ComObject WScript.Shell
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = $shell.CreateShortcut((Join-Path $startup "Flexy Way.lnk"))
$shortcut.TargetPath = Join-Path $PSScriptRoot "START_FLEXY_WAY_SILENT.vbs"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = "Start Flexy Way when Windows signs in"
$shortcut.Save()
Write-Host "Flexy Way startup shortcut created."
