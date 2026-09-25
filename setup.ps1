param([switch]$SkipRegister)
$ErrorActionPreference = 'Stop'
$hiveRoot = $PSScriptRoot
$hivePython = Join-Path $hiveRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $hivePython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv (Join-Path $hiveRoot '.venv')
    } else {
        & python -m venv (Join-Path $hiveRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $hivePython -m pip install -r (Join-Path $hiveRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $hiveRoot 'hive.local.json'))) {
    & $hivePython (Join-Path $hiveRoot 'hive.py') init
    if ($LASTEXITCODE -ne 0) { throw 'Hive initialization failed.' }
}
if (-not $SkipRegister) {
    & $hivePython (Join-Path $hiveRoot 'scripts/register_agents.py')
    if ($LASTEXITCODE -ne 0) { throw 'Agent registration failed.' }
}
Write-Output "HiveMind ready. Run: & '$hivePython' '$hiveRoot/hive.py' doctor"
Write-Output "Obsidian vault: $hiveRoot/vault"
