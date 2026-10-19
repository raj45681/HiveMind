[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$SkipRegister,
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$SetupArguments
)
$ErrorActionPreference = 'Stop'
$hivePython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$bootstrap = Join-Path $PSScriptRoot 'bootstrap.py'
$forwarded = @($SetupArguments)
if ($SkipRegister) { $forwarded += '--skip-register' }
if (Test-Path -LiteralPath $hivePython) {
    & $hivePython $bootstrap @forwarded
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $bootstrap @forwarded
} else {
    & python $bootstrap @forwarded
}
exit $LASTEXITCODE
