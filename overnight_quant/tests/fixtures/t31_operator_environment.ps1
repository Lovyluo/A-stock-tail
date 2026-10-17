param([string]$Payload,[string]$OutputPath,[string]$Marker)
# Synthetic fixture: filesystem-only, never calls Windows service or source APIs.
$ErrorActionPreference = 'Stop'
$data = Get-Content -Raw -Encoding UTF8 -LiteralPath $Payload | ConvertFrom-Json
$global:LASTEXITCODE = if ($data.seed -eq 'unset') { $null } else { [int]$data.seed }
[IO.File]::WriteAllText($Marker,'test_only environment called')
if ($data.stderr) { [Console]::Error.WriteLine('synthetic diagnostic on stderr') }
if ($data.write_result) {
    # Keep the synthetic JSON bytes; PS7 otherwise coerces ISO strings to local DateTime.
    $text = if ($data.malformed) { '{broken' } else { [string]$data.result_json }
    [IO.File]::WriteAllText($OutputPath,$text,[Text.UTF8Encoding]::new($false))
}
if ($data.mode -eq 'throw') { throw 'synthetic environment crash' }
if ($data.mode -eq 'exit') { exit ([int]$data.exit_code) }
# Deliberately no explicit exit: verifies the caller reads the actual process code,
# not the stale in-process LASTEXITCODE seeded above.
Write-Output 'test_only environment completed'
