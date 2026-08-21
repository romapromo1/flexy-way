$ErrorActionPreference = "Stop"
$runtimeDir = Join-Path $PSScriptRoot ".runtime"
$processFile = Join-Path $runtimeDir "processes.json"

if (-not (Test-Path -LiteralPath $processFile)) {
    Write-Host "No Flexy Way process record was found."
    exit 0
}

$records = Get-Content -Raw -LiteralPath $processFile | ConvertFrom-Json
foreach ($record in @($records.bot, $records.server)) {
    if (-not $record -or -not $record.id) { continue }
    $process = Get-Process -Id ([int]$record.id) -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $actualStarted = $process.StartTime.ToUniversalTime()
    $expectedStarted = [DateTime]::Parse($record.started).ToUniversalTime()
    $sameLaunch = [Math]::Abs(($actualStarted - $expectedStarted).TotalSeconds) -lt 2
    $allowedName = $process.ProcessName -in @("node", "python", "pythonw")
    if ($sameLaunch -and $allowedName) {
        Stop-Process -Id $process.Id
    }
}

Remove-Item -LiteralPath $processFile -Force
Write-Host "Flexy Way background services stopped."
