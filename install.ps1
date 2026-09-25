param(
    [string]$Project = (Get-Location).Path,
    [string]$Name = '',
    [string]$MemoryUrl = '',
    [switch]$Preview,
    [switch]$SkipRegister
)
$ErrorActionPreference = 'Stop'
$hiveRoot = $PSScriptRoot
$hivePython = Join-Path $hiveRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $hivePython)) {
    if ($Preview) { throw 'Run setup.ps1 once before previewing project installation.' }
    & (Join-Path $hiveRoot 'setup.ps1') -SkipRegister
}
if (-not (Test-Path -LiteralPath $hivePython)) { throw 'HiveMind Python environment is missing.' }
if (-not $Preview -and $MemoryUrl) {
    $enrollArgs = @((Join-Path $hiveRoot 'hive.py'), 'enroll-cloud')
    if ($MemoryUrl) { $enrollArgs += @('--url', $MemoryUrl) }
    & $hivePython @enrollArgs
    if ($LASTEXITCODE -ne 0) { throw 'Cloud memory connection failed.' }
}
$hiveArgs = @((Join-Path $hiveRoot 'hive.py'), 'attach', $Project)
if ($Name) { $hiveArgs += @('--name', $Name) }
if ($Preview) { $hiveArgs += '--dry-run' }
if ($SkipRegister) { $hiveArgs += '--skip-register' }
& $hivePython @hiveArgs
if ($LASTEXITCODE -ne 0) { throw 'HiveMind project installation failed; see the error above.' }
