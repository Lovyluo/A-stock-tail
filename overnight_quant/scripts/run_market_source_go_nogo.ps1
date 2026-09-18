[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Date,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][string]$CalendarContract,
    [Parameter(Mandatory = $true)][string]$CalendarFileSha256,
    [string]$Codes = '000001,000333,600000,600519,601318',
    [string]$CutoffClock = '13:30:00',
    [string]$TestOnlyEnvironmentFixture = '',
    [string]$PythonExe = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$python = $PythonExe
if ([string]::IsNullOrWhiteSpace($python)) {
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
}
if ($python -ne 'python' -and -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = 'python'
}
$script = Join-Path $ProjectRoot 'overnight_quant\scripts\run_market_source_go_nogo.py'
$arguments = @(
    $script,
    '--date', $Date,
    '--codes', $Codes,
    '--output', $Output,
    '--calendar-contract', $CalendarContract,
    '--calendar-file-sha256', $CalendarFileSha256,
    '--cutoff-clock', $CutoffClock
)
if (-not [string]::IsNullOrWhiteSpace($TestOnlyEnvironmentFixture)) {
    $arguments += @(
        '--test-only-environment-fixture',
        $TestOnlyEnvironmentFixture
    )
}

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($previousPythonPath)) {
        $ProjectRoot
    } else {
        "$ProjectRoot;$previousPythonPath"
    }
    & $python @arguments
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
