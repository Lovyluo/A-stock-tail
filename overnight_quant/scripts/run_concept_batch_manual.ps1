param(
    [string]$Day, [string]$ApprovedSha, [string]$Output, [string]$BatchId,
    [string]$Fixture, [switch]$Network, [string]$Authorization, [string]$AuthorizationSha,
    [string]$TestState, [string]$PythonPath = 'D:\A-stock\.venv\Scripts\python.exe',
    [ValidateSet('None','StartupOverrun','ReportOverrun','AfterReportExit','ConfirmationWriteFailure','AfterConfirmationExit')]
    [string]$TestScenario = 'None'
)
$ErrorActionPreference = 'Stop'
$started = [Diagnostics.Stopwatch]::GetTimestamp()
$frequency = [Diagnostics.Stopwatch]::Frequency
$contract = '6cb0e62da380c9cafb7950ae554dded368b4a286cea3b7cfa8e6d6c1d4f66a26'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$state = if ($TestState) { [IO.Path]::GetFullPath($TestState) } else { [IO.Path]::GetFullPath((Join-Path $root 'overnight_quant\data\cache\concept-batch-observation')) }
$outputPath = if ($Output) { [IO.Path]::GetFullPath($Output) } else { '' }
$process = $null; $owned = $false; $treeClean = $true; $launched = $false; $knownZero = $false; $offset = [long]0
$lock = Join-Path $state 'launcher.lock'

function Tick { return [Diagnostics.Stopwatch]::GetTimestamp() + $script:offset }
function RemainingMs { return [int][Math]::Max(0, [Math]::Floor(90000 - ((Tick) - $started) * 1000.0 / $frequency)) }
function HashFile([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $stream = [IO.File]::OpenRead($Path); try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLowerInvariant() } finally { $stream.Dispose() } }
    finally { $algorithm.Dispose() }
}
function WriteNewBytes([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function WriteNewJson([string]$Path, $Object) {
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $json = $Object | ConvertTo-Json -Depth 30 -Compress
    WriteNewBytes $temporary ([Text.UTF8Encoding]::new($false).GetBytes($json))
    [IO.File]::Move($temporary, $Path)
}
function QuoteArgument([string]$Value) {
    $builder = [Text.StringBuilder]::new(); [void]$builder.Append('"'); $slashes = 0
    foreach ($c in $Value.ToCharArray()) {
        if ($c -eq '\') { $slashes++; continue }
        if ($c -eq '"') { [void]$builder.Append(('\' * (2 * $slashes + 1))); [void]$builder.Append('"') }
        else { [void]$builder.Append(('\' * $slashes)); [void]$builder.Append($c) }
        $slashes = 0
    }
    [void]$builder.Append(('\' * (2 * $slashes))); [void]$builder.Append('"'); return $builder.ToString()
}
function MarkUnconfirmed([string]$Reason) {
    $path = Join-Path $state 'cleanup-incomplete.json'
    if (!(Test-Path -LiteralPath $path)) {
        try { WriteNewJson $path ([ordered]@{status='CLEANUP_UNCONFIRMED';reason=$Reason;batch_id=$BatchId;output=$outputPath}) } catch { }
    }
}
function StopTreeBounded($Target) {
    $cleanupMs = if ($TestScenario -eq 'StartupOverrun' -and $Fixture) { 1500 } else { RemainingMs }
    try {
        if ($cleanupMs -le 0) { throw 'cleanup_budget_exhausted' }
        $killInfo = [Diagnostics.ProcessStartInfo]::new(); $killInfo.FileName = 'taskkill.exe'
        $killInfo.Arguments = "/PID $($Target.Id) /T /F"; $killInfo.UseShellExecute = $false
        $killInfo.CreateNoWindow = $true; $killInfo.RedirectStandardOutput = $true; $killInfo.RedirectStandardError = $true
        $killer = [Diagnostics.Process]::Start($killInfo)
        try {
            if (!$killer.WaitForExit([Math]::Min(1500, $cleanupMs))) {
                $killer.Kill()
                $killerWait = if ($TestScenario -eq 'StartupOverrun' -and $Fixture) { 250 } else { [Math]::Min(250, (RemainingMs)) }
                [void]$killer.WaitForExit($killerWait)
            }
        } finally { $killer.Dispose() }
    } catch { }
    $targetWait = if ($TestScenario -eq 'StartupOverrun' -and $Fixture) { 250 } else { [Math]::Min(250, (RemainingMs)) }
    try { if (!$Target.HasExited) { $Target.Kill() }; [void]$Target.WaitForExit($targetWait) } catch { }
}

try {
    if (!$Fixture -and !$Network) { [ordered]@{status='NETWORK_NOT_REQUESTED';requests_dispatched=0} | ConvertTo-Json -Compress; exit 0 }
    if (($Fixture -and $Network) -or ($TestState -and !$Fixture) -or ($TestScenario -ne 'None' -and (!$Fixture -or $Network))) { throw 'fixture_or_network_contract_invalid' }
    if ($Network -and (!$Authorization -or !$AuthorizationSha)) { throw 'external_authorization_anchor_required' }
    if (!$BatchId -or $BatchId -notmatch '^[A-Za-z0-9_-]{1,64}$' -or !$Day -or !$ApprovedSha -or !$outputPath) { throw 'required_input_missing' }
    if (Test-Path -LiteralPath $outputPath) { throw 'output_exists' }
    [void][IO.Directory]::CreateDirectory($state)
    if ((Test-Path -LiteralPath (Join-Path $state 'cleanup-incomplete.json')) -or (Test-Path -LiteralPath (Join-Path $state 'active.lock'))) { throw 'persistent_cleanup_or_active_block' }
    $nonce = [Guid]::NewGuid().ToString('N')
    WriteNewBytes $lock ([Text.Encoding]::UTF8.GetBytes($nonce)); $owned = $true
    $ticketDir = Join-Path (Join-Path $state 'launcher-tickets') $BatchId
    if (Test-Path -LiteralPath $ticketDir) { throw 'launcher_ticket_already_exists' }
    [void][IO.Directory]::CreateDirectory($ticketDir)
    $ticketPath = Join-Path $ticketDir 'ticket.json'
    $ticket = [ordered]@{schema_version='concept_batch_launcher_v2';contract_hash=$contract;nonce=$nonce;
        launcher_pid=$PID;batch_id=$BatchId;day=$Day;approved_sha=$ApprovedSha;output=$outputPath;
        synthetic_test_only=[bool]$Fixture;launcher_started_ticks=$started;stopwatch_frequency=$frequency}
    WriteNewJson $ticketPath $ticket
    $ticketHash = HashFile $ticketPath
    $arguments = @('-m','overnight_quant.scripts.run_concept_batch','--day',$Day,'--approved-sha',$ApprovedSha,
        '--output',$outputPath,'--batch-id',$BatchId,'--launcher-ticket',$ticketPath)
    if ($Fixture) { $arguments += @('--fixture',[IO.Path]::GetFullPath($Fixture)) }
    if ($Network) { $arguments += '--network' }
    if ($Authorization) { $arguments += @('--authorization',[IO.Path]::GetFullPath($Authorization)) }
    if ($AuthorizationSha) { $arguments += @('--authorization-sha',$AuthorizationSha) }
    if ($TestState) { $arguments += @('--test-state',$state) }
    $info = [Diagnostics.ProcessStartInfo]::new(); $info.FileName = $PythonPath
    $info.Arguments = (($arguments | ForEach-Object { QuoteArgument ([string]$_) }) -join ' ')
    $info.WorkingDirectory = $root; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false, $true)
    $info.StandardErrorEncoding = $info.StandardOutputEncoding
    $info.EnvironmentVariables['CONCEPT_BATCH_LAUNCH_TOKEN'] = $nonce
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8:strict'
    if ((RemainingMs) -le 2000) { throw 'insufficient_start_budget' }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    if (!$process.Start()) { throw 'python_start_failed' }
    $launched = $true; $treeClean = $false
    $stdoutTask = $process.StandardOutput.ReadToEndAsync(); $stderrTask = $process.StandardError.ReadToEndAsync()
    if ($TestScenario -eq 'StartupOverrun') { $offset = 90 * $frequency }
    $wait = [Math]::Max(0, (RemainingMs) - 2000)
    if (!$process.WaitForExit([int]$wait)) {
        MarkUnconfirmed 'launcher_timeout_process_tree_unknown'
        StopTreeBounded $process
        throw 'launcher_timeout_cleanup_unconfirmed'
    }
    $remaining = RemainingMs
    if (!$stdoutTask.Wait($remaining) -or !$stderrTask.Wait((RemainingMs))) { MarkUnconfirmed 'launcher_pipe_cleanup_unknown'; throw 'launcher_pipe_timeout' }
    $stdout = $stdoutTask.Result; $stderr = $stderrTask.Result
    WriteNewBytes (Join-Path $ticketDir 'python.stdout') ([Text.UTF8Encoding]::new($false).GetBytes($stdout))
    WriteNewBytes (Join-Path $ticketDir 'python.stderr') ([Text.UTF8Encoding]::new($false).GetBytes($stderr))
    $candidate = $stdout | ConvertFrom-Json
    $treeClean = (($candidate.supervisor_cleanup_complete -eq $true) -or ($candidate.requests_dispatched -eq 0))
    if (!$treeClean) { MarkUnconfirmed 'supervisor_cleanup_unknown'; throw 'supervisor_cleanup_unconfirmed' }
    if ($owned -and [IO.File]::ReadAllText($lock) -eq $nonce) { [IO.File]::Delete($lock); $owned = $false }
    if ($candidate.status -eq 'FAILED' -and $candidate.supervisor_cleanup_complete -eq $true -and
        $candidate.requests_dispatched -eq 0 -and $candidate.request_count_status -eq 'KNOWN_ZERO') {
        $knownZero = $true
        if ($candidate.error) { throw [string]$candidate.error }
    }
    if ($process.ExitCode -ne 3 -or $candidate.status -ne 'EXECUTION_CANDIDATE' -or $candidate.candidate_status -ne 'PASSED') { throw 'capture_not_successful' }
    if ($candidate.batch_id -ne $BatchId -or $candidate.output -ne $outputPath -or $candidate.contract_hash -ne $contract -or $candidate.supervisor_cleanup_complete -ne $true) { throw 'candidate_identity_invalid' }
    $reportPath = Join-Path $outputPath 'returned-report.json'
    WriteNewBytes $reportPath ([Text.UTF8Encoding]::new($false).GetBytes($stdout))
    $reportHash = HashFile $reportPath
    $readback = [IO.File]::ReadAllText($reportPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($readback.batch_id -ne $BatchId -or $readback.status -ne 'EXECUTION_CANDIDATE') { throw 'report_readback_invalid' }
    if ($TestScenario -eq 'AfterReportExit') { [Environment]::Exit(19) }
    if ($TestScenario -eq 'ReportOverrun') { $offset = 90 * $frequency }
    $checked = Tick
    if ($checked -lt $started -or ([decimal]($checked - $started) -ge ([decimal]90 * $frequency))) { throw 'outer_execution_deadline_exceeded' }
    $confirmationPath = Join-Path $outputPath 'final-confirmation.json'
    $confirmation = [ordered]@{schema_version='concept_batch_outer_confirmation_v2';status='EXECUTION_SUCCEEDED';
        contract_hash=$contract;batch_id=$BatchId;output=$outputPath;report_sha256=$reportHash;
        ticket_path=$ticketPath;ticket_sha256=$ticketHash;
        synthetic_test_only=[bool]$Fixture;launcher_started_ticks=$started;report_checked_ticks=$checked;
        stopwatch_frequency=$frequency;execution_elapsed_ms=($checked - $started) * 1000.0 / $frequency;
        confirmed_at=[DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)).ToString('o');caller_received_result='UNKNOWN'}
    if ($TestScenario -eq 'ConfirmationWriteFailure') { throw 'fixture_confirmation_write_failure' }
    $confirmationTemp = $confirmationPath + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $confirmationJson = $confirmation | ConvertTo-Json -Depth 30 -Compress
    WriteNewBytes $confirmationTemp ([Text.UTF8Encoding]::new($false).GetBytes($confirmationJson))
    $confirmHash = HashFile $confirmationTemp
    $confirmed = [IO.File]::ReadAllText($confirmationTemp, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($confirmed.report_sha256 -ne $reportHash -or $confirmed.status -ne 'EXECUTION_SUCCEEDED') { throw 'confirmation_readback_invalid' }
    [IO.File]::Move($confirmationTemp, $confirmationPath)
    if ($TestScenario -eq 'AfterConfirmationExit') { [Environment]::Exit(19) }
    [ordered]@{status='PASSED';batch_id=$BatchId;output=$outputPath;report_path=$reportPath;
        report_sha256=$reportHash;confirmation_sha256=$confirmHash;execution_elapsed_ms=$confirmation.execution_elapsed_ms;
        requests_dispatched=$candidate.requests_dispatched;synthetic_test_only=[bool]$Fixture} | ConvertTo-Json -Compress
    exit 0
} catch {
    if (!$treeClean) { MarkUnconfirmed 'launcher_failure_cleanup_unknown' }
    [ordered]@{status='FAILED';error=$_.Exception.Message;requests_dispatched=$(if(!$launched -or $knownZero){0}else{$null});
        request_count_status=$(if(!$launched -or $knownZero){'KNOWN_ZERO'}else{'UNKNOWN'});output=$outputPath} | ConvertTo-Json -Compress
    exit 2
} finally {
    if ($process) { $process.Dispose() }
    if ($owned -and $treeClean -and (Test-Path -LiteralPath $lock)) {
        try { if ([IO.File]::ReadAllText($lock) -eq $nonce) { [IO.File]::Delete($lock) } } catch { }
    }
}
