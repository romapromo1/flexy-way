$ErrorActionPreference = "Stop"
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = $shell.CreateShortcut((Join-Path $desktop "Flexy Way.lnk"))
$shortcut.TargetPath = Join-Path $PSScriptRoot "START_FLEXY_WAY.bat"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = "Start the Flexy Way festival game"
$shortcut.Save()
Write-Host "Desktop shortcut created."
