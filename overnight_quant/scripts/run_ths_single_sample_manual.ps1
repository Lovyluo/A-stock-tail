param(
    [string]$TargetDate, [string]$ApprovedSha, [string]$Output, [string]$BatchId,
    [string]$Approval, [string]$ApprovalSha, [switch]$Execute,
    [string]$Fixture, [string]$TestState,
    [ValidateSet('None','SerializationOverrun')][string]$TestScenario = 'None',
    [string]$PythonPath = 'D:\A-stock\.venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$start = [Diagnostics.Stopwatch]::GetTimestamp()
$frequency = [Diagnostics.Stopwatch]::Frequency
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$state = if ($TestState -and $Fixture -and !$Execute) { [IO.Path]::GetFullPath($TestState) } else { Join-Path $root 'overnight_quant\data\cache\ths-single-sample' }
$process = $null; $cleaned = $false
$offset = 0
function Elapsed { return ([Diagnostics.Stopwatch]::GetTimestamp() - $start) * 1000.0 / $frequency + $script:offset }
function Quote([string]$Value) {
    $builder = [Text.StringBuilder]::new(); [void]$builder.Append('"'); $slashes = 0
    foreach ($c in $Value.ToCharArray()) {
        if ($c -eq '\') { $slashes++; continue }
        if ($c -eq '"') { [void]$builder.Append(('\' * (2 * $slashes + 1))); [void]$builder.Append('"') }
        else { [void]$builder.Append(('\' * $slashes)); [void]$builder.Append($c) }
        $slashes = 0
    }
    [void]$builder.Append(('\' * (2 * $slashes))); [void]$builder.Append('"'); return $builder.ToString()
}
function Hash([byte[]]$Bytes) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
function SaveNew([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes,0,$Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
try {
    if (!$Execute -and !$Fixture) { '{"status":"NETWORK_NOT_REQUESTED","actual_http_requests":0,"workers":0}'; exit 0 }
    if (($Execute -and $Fixture) -or ($TestState -and !$Fixture) -or ($TestScenario -ne 'None' -and (!$Fixture -or $Execute))) { throw 'fixture_or_live_required' }
    $arguments = @('-m','overnight_quant.scripts.run_ths_single_sample','--target-date',$TargetDate,
        '--approved-sha',$ApprovedSha,'--output',$Output,'--batch-id',$BatchId,'--launcher-start',
        ([double]$start / $frequency).ToString('R',[Globalization.CultureInfo]::InvariantCulture))
    if ($Execute) { $arguments += '--execute' }
    foreach ($pair in @(@('--approval',$Approval),@('--approval-sha',$ApprovalSha),@('--fixture',$Fixture),@('--test-state',$TestState))) {
        if ($pair[1]) { $arguments += $pair }
    }
    $info = [Diagnostics.ProcessStartInfo]::new(); $info.FileName = $PythonPath
    $info.Arguments = (($arguments | ForEach-Object { Quote ([string]$_) }) -join ' ')
    $info.WorkingDirectory = $root; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false,$true)
    $info.StandardErrorEncoding = $info.StandardOutputEncoding
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8:strict'
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit([int][Math]::Max(0,9000-(Elapsed)))) { throw 'launcher_deadline_cleanup_unconfirmed' }
    if (!$stdout.Wait([int][Math]::Max(0,9500-(Elapsed))) -or !$stderr.Wait([int][Math]::Max(0,9500-(Elapsed)))) { throw 'launcher_pipe_cleanup_unconfirmed' }
    $result = $stdout.Result | ConvertFrom-Json
    $cleaned = ($result.supervisor_cleanup_complete -eq $true -or $result.requests_attempted -eq 0)
    if ($result.output -and (Test-Path -LiteralPath $result.output -PathType Container)) {
        $bytes = [Text.UTF8Encoding]::new($false,$true).GetBytes($stdout.Result)
        SaveNew (Join-Path $result.output 'python-result.json') $bytes
        $result | Add-Member -NotePropertyName launcher_report_sha256 -NotePropertyValue (Hash ([IO.File]::ReadAllBytes((Join-Path $result.output 'python-result.json'))))
    }
    $elapsed = Elapsed
    $result | Add-Member -NotePropertyName launcher_version -NotePropertyValue 'ths_single_day_sample_v1'
    $result | Add-Member -NotePropertyName launcher_elapsed_ms -NotePropertyValue $elapsed
    if ($elapsed -ge 10000 -or $process.ExitCode -ne 0) { $result.status = 'FAILED' }
    $result | Add-Member -NotePropertyName serialized_before_deadline -NotePropertyValue $true
    $serialized = $result | ConvertTo-Json -Depth 35 -Compress
    if ($TestScenario -eq 'SerializationOverrun') { $offset = 10001 }
    if ((Elapsed) -ge 10000) { throw 'receipt_serialization_deadline' }
    $serialized
    if ($result.status -eq 'SAMPLE_PARSED') { exit 0 } else { exit 2 }
} catch {
    if ($process -and !$process.HasExited) {
        # The Python supervisor normally owns cleanup. Its failure is never called clean.
        try { $process.Kill(); [void]$process.WaitForExit([int][Math]::Max(0,10000-(Elapsed))) } catch { }
    }
    if ($process -and !$cleaned) {
        try {
            [void][IO.Directory]::CreateDirectory($state)
            SaveNew (Join-Path $state 'cleanup-incomplete.json') ([Text.Encoding]::UTF8.GetBytes('{"status":"LAUNCHER_CLEANUP_UNCONFIRMED"}'))
        } catch { }
    }
    @{status='FAILED';error=[string]$_.Exception.Message;actual_http_requests=$(if($Fixture){0}else{$null})} | ConvertTo-Json -Compress
    exit 2
} finally { if ($process) { $process.Dispose() } }
