param(
    [Parameter(Mandatory=$true)][string]$OpsDirectory,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$MinuteCapture,
    [switch]$ProtocolDiagnostic,
    [string]$ProtocolDiagnosticRoot,
    [ValidateSet('mootdx_protocol_diagnostic_worker_v1','mootdx_protocol_differential_worker_v1')]
    [string]$ProtocolDiagnosticWorkerVersion = 'mootdx_protocol_diagnostic_worker_v1',
    [int]$AllowedControllerPid = 0,
    [string]$ControllerPythonPath,
    [string]$ControllerBasePythonPath,
    [string]$ControllerWorkingDirectory,
    [string]$ProjectRoot,
    [string]$MinuteCaptureRoot,
    [string]$TradeDate
)
$ErrorActionPreference = 'Stop'
$code = 2
$serviceRunning = $null
$serviceCheckStatus = 'NOT_QUERIED'
$controllerDiagnostics = @()
function Get-FileSha256([string]$Path) {
    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::OpenRead($Path)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
    } finally {
        if ($null -ne $algorithm) { $algorithm.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
function Write-Result($Path,$Value) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 15))
    $f = [IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $f.Write($bytes,0,$bytes.Length);$f.Flush($true) } finally { $f.Dispose() }
}
function Test-EquivalentJson($Left,$Right) {
    if ($null -eq $Left -or $null -eq $Right) { return ($null -eq $Left -and $null -eq $Right) }
    if ($Left -is [pscustomobject] -and $Right -is [pscustomobject]) {
        $a = @($Left.PSObject.Properties.Name | Sort-Object)
        $b = @($Right.PSObject.Properties.Name | Sort-Object)
        if (($a -join "`n") -cne ($b -join "`n")) { return $false }
        foreach ($name in $a) { if (!(Test-EquivalentJson $Left.$name $Right.$name)) { return $false } }
        return $true
    }
    if ($Left -is [array] -and $Right -is [array]) {
        if ($Left.Count -ne $Right.Count) { return $false }
        for ($i=0;$i -lt $Left.Count;$i++) { if (!(Test-EquivalentJson $Left[$i] $Right[$i])) { return $false } }
        return $true
    }
    return $Left.Equals($Right)
}
try {
    if ($ProtocolDiagnostic) { $MinuteCapture = $true }
    if ($MinuteCapture) {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
    }
    if (Test-Path -LiteralPath $OutputPath) { throw 'Immutable environment output exists' }
    $intent = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $OpsDirectory 'intent.json') | ConvertFrom-Json
    $since = [DateTimeOffset]::Parse([string]$intent.created_at).LocalDateTime
    if ($MinuteCapture) {
        if (!$intent.environment_observation_since) { throw 'Minute environment observation start missing' }
        $since = [DateTimeOffset]::Parse([string]$intent.environment_observation_since).LocalDateTime
    }
    $issues = @()
    try { $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop) }
    catch {
        $controllerDiagnostics = @(@{stage='process_query';pid=$AllowedControllerPid;parent_pid=$null
            process_name=$null;failed_item='PROCESS_QUERY_FAILED';classification='REJECTED'})
        throw 'Process inventory query failed'
    }
    $workers = @($allProcesses | Where-Object {
        $_.ProcessId -ne $PID -and $_.Name -match '^(python|pythonw|powershell|pwsh|dotnet)(\.exe)?$' -and
        $_.CommandLine -match 'market_httpclient_worker\.ps1|market_source_worker\.py|run_complete_industry_audit\.py|run_market_source_validation\.py|pytest|run_minute_probe_watchdog\.ps1'
    } | Select-Object ProcessId,ParentProcessId,Name,CommandLine)
    if ($MinuteCapture) {
        if (!$ProjectRoot -or !$MinuteCaptureRoot -or !$TradeDate) { throw 'Minute environment arguments missing' }
        # Match actual entry/module tokens, not every Python process or a word in a note.
        $pattern = '(?:[\\/]|^|[\s"])' +
            '(?:run_minute_protocol_diagnostic|minute_protocol_differential_worker|minute_protocol_worker|run_minute_capture_audit|run_minute_local_prepare_audit|minute_capture_worker|minute_local_prepare_worker|minute_probe_worker|run_minute_probe_watchdog|' +
            'run_market_source_validation|run_complete_industry_audit|market_source_worker)\.(?:py|ps1)(?:["\s]|$)' +
            '|(?:^|[\s"])-m"?\s*"?overnight_quant\.(?:data\.(?:minute_protocol_differential_worker|minute_protocol_worker|minute_capture_worker|minute_local_prepare_worker|minute_probe_worker|market_source_worker)' +
            '|scripts\.(?:run_minute_protocol_diagnostic|run_minute_capture_audit|run_minute_local_prepare_audit|run_market_source_validation|run_complete_industry_audit))(?:[\s"]|$)' +
            '|inspect_industry_audit_environment\.ps1.*-MinuteCapture'
        $allowed = @()
        $ordinaryBoundary = @()
        if ($ProtocolDiagnostic) { . (Join-Path $PSScriptRoot 'protocol_controller_identity.ps1') }
        if ($ProtocolDiagnostic -and $AllowedControllerPid -gt 0) {
            $roles = Resolve-ProtocolControllers $allProcesses $AllowedControllerPid $ControllerPythonPath `
                $ControllerBasePythonPath $ControllerWorkingDirectory $ProjectRoot
            $allowed = @($roles.allowed)
            $ordinaryBoundary = @($roles.ordinary_boundary)
            $controllerDiagnostics = @($roles.diagnostics)
        }
        $candidates = @($allProcesses | Where-Object {
            $_.ProcessId -ne $PID -and $_.ProcessId -notin $allowed -and $_.ProcessId -notin $ordinaryBoundary -and $_.Name -match '^(python|pythonw|powershell|pwsh|dotnet)(\.exe)?$' -and
            $_.CommandLine -match $pattern
        })
        $ids = @(foreach ($candidate in $candidates) {
            if ($ProtocolDiagnostic -and $candidate.Name -match '^python(w)?\.exe$') {
                try {
                    $words = ConvertFrom-ControllerCommandLine $candidate.CommandLine
                    $entry = Get-ControllerEntry $words $ProjectRoot
                    $direct = ($entry.kind -eq 'file' -and [IO.Path]::GetFileName($entry.value) -match
                        '^(run_minute_protocol_diagnostic|minute_protocol_differential_worker|minute_protocol_worker|run_minute_capture_audit|run_minute_local_prepare_audit|minute_capture_worker|minute_local_prepare_worker|minute_probe_worker|run_minute_probe_watchdog|run_market_source_validation|run_complete_industry_audit|market_source_worker)\.py$') -or
                        ($entry.kind -eq 'module' -and $entry.value -match '^overnight_quant\.(data\.(minute_protocol_differential_worker|minute_protocol_worker|minute_capture_worker|minute_local_prepare_worker|minute_probe_worker|market_source_worker)|scripts\.(run_minute_protocol_diagnostic|run_minute_capture_audit|run_minute_local_prepare_audit|run_market_source_validation|run_complete_industry_audit))$')
                    # Text carried as data is not an invoked project entry. Unknown parsing still blocks.
                    if (!$direct -and ($entry.kind -in @('file','module') -or
                            ($entry.kind -eq 'option' -and $entry.value.StartsWith('-c')))) { continue }
                } catch { }
            }
            [int]$candidate.ProcessId
        })
        do {
            $more = @($allProcesses | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids } |
                ForEach-Object { [int]$_.ProcessId })
            $ids += $more
        } while ($more.Count)
        $workers = @($allProcesses | Where-Object { $_.ProcessId -in $ids } |
            Select-Object ProcessId,ParentProcessId,Name,CommandLine)
    }
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
        $_.TaskName -match 'AStock|A-stock|MarketSource|IndustryAudit' -or
        (@($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' ') -match 'A-stock|market_source|complete_industry' -or
        ($MinuteCapture -and ($_.TaskName -match 'MinuteCapture' -or
            (@($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' ') -match 'minute_capture|minute_local_prepare|minute_protocol'))
    } | ForEach-Object { [ordered]@{TaskPath=$_.TaskPath;TaskName=$_.TaskName;State=[string]$_.State;Enabled=[bool]$_.Settings.Enabled} })
    if ($workers.Count) { $issues += 'Residual worker present' }
    if ($ProtocolDiagnostic) {
        $workers = @($workers | ForEach-Object {
            $algorithm = [Security.Cryptography.SHA256]::Create()
            try { $hash = ([BitConverter]::ToString($algorithm.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes([string]$_.CommandLine)))).Replace('-','').ToLowerInvariant() }
            finally { $algorithm.Dispose() }
            [pscustomobject]@{ProcessId=$_.ProcessId;ParentProcessId=$_.ParentProcessId;Name=$_.Name;command_line_sha256=$hash}
        })
    }
    if (@($tasks | Where-Object { $_.Enabled -or $_.State -eq 'Running' }).Count) { $issues += 'Active project task present' }
    $pendingPreparations = @()
    if ($MinuteCapture -and (Test-Path -LiteralPath $MinuteCaptureRoot)) {
        foreach ($dir in Get-ChildItem -LiteralPath $MinuteCaptureRoot -Directory -ErrorAction Stop) {
            foreach ($started in Get-ChildItem -LiteralPath $dir.FullName -Filter 'wave-*.started.json' -File -ErrorAction Stop) {
                try {
                    $opening = Get-Content -LiteralPath $started.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($opening.task.worker_version -cne 'mootdx_local_prepare_worker_v2' -or
                        $opening.task.code_sha -notmatch '^[0-9a-f]{40}$' -or
                        $opening.task.plan_hash -notmatch '^[0-9a-f]{64}$' -or
                        !$opening.task.batch_id -or ($opening.task.wave -isnot [int] -and $opening.task.wave -isnot [long]) -or
                        $opening.task.wave -lt 0 -or $opening.task.wave -gt 3 -or
                        $dir.Name -cne $opening.task.trade_date -or
                        $started.Name -cne "wave-$($opening.task.wave).started.json") { throw 'Unknown preparation receipt identity' }
                    $closingPath = Join-Path $dir.FullName $started.Name.Replace('.started.json','.json')
                    $closing = Get-Content -LiteralPath $closingPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
                    $closedTask = $closing.result.task | Select-Object * -ExcludeProperty launch_root_pid
                    if ($closing.result.cleanup_complete -isnot [bool] -or !$closing.result.cleanup_complete -or
                        $closing.start_receipt_sha256 -cne (Get-FileSha256 $started.FullName) -or
                        !(Test-EquivalentJson $opening.task $closedTask)) { throw 'Unconfirmed cleanup receipt' }
                } catch {
                    $pendingPreparations += $started.FullName
                    $issues += "Pending minute preparation receipt: $($started.FullName)"
                }
            }
        }
    }
    if ($ProtocolDiagnostic) {
        if (!$ProtocolDiagnosticRoot) { throw 'Protocol diagnostic root missing' }
        if (Test-Path -LiteralPath (Join-Path $ProtocolDiagnosticRoot 'cleanup-incomplete.json')) { $issues += 'Protocol cleanup incomplete' }
        if (Test-Path -LiteralPath $ProtocolDiagnosticRoot) {
            foreach ($dir in Get-ChildItem -LiteralPath $ProtocolDiagnosticRoot -Directory -ErrorAction Stop) {
                if (!(Test-Path -LiteralPath (Join-Path $dir.FullName 'intent.json')) -and
                    !(Test-Path -LiteralPath (Join-Path $dir.FullName 'request.started.json'))) { continue }
                try {
                    $null = Get-Content -LiteralPath (Join-Path $dir.FullName 'intent.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
                    $startPath = Join-Path $dir.FullName 'request.started.json'
                    $opening = Get-Content -LiteralPath $startPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
                    $closed = Get-Content -LiteralPath (Join-Path $dir.FullName 'request.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
                    $closedTask = $closed.result.task | Select-Object * -ExcludeProperty launch_root_pid,launch
                    if ($opening.task.worker_version -cne $ProtocolDiagnosticWorkerVersion -or
                        $opening.task.trade_date -cne $dir.Name -or $closed.result.cleanup_complete -isnot [bool] -or
                        !$closed.result.cleanup_complete -or
                        $closed.start_receipt_sha256 -cne (Get-FileSha256 $startPath) -or
                        !(Test-EquivalentJson $opening.task $closedTask)) { throw 'Protocol cleanup receipt invalid' }
                } catch {
                    $pendingPreparations += $dir.FullName
                    $issues += "Pending protocol preparation receipt: $($dir.FullName)"
                }
            }
        }
    }
    $locks = @()
    $journals = @()
    $temp = [IO.Path]::GetTempPath()
    foreach ($name in @('a-stock-t25-global-executor.lock','a-stock-t25-global-executor.stage')) {
        $path = Join-Path $temp $name
        if (Test-Path -LiteralPath $path) {
            $issues += "Executor lock exists: $path"
            $copy = Join-Path $OpsDirectory ([Guid]::NewGuid().ToString('N') + '-' + $name)
            Copy-Item -LiteralPath $path -Destination $copy
            $locks += @{path=$path;preserved_copy=$copy;sha256=(Get-FileSha256 -Path $path)}
        }
    }
    foreach ($name in @('http-start-state','a-stock-http-start-v1')) {
        $dir = Join-Path $temp $name
        if (!(Test-Path -LiteralPath $dir)) { continue }
        foreach ($file in Get-ChildItem -LiteralPath $dir -Force) {
            if ($file.PSIsContainer) { $issues += "Unexpected journal directory: $($file.FullName)"; continue }
            $valid = $false
            $tail = $null
            try {
                $raw = [IO.File]::ReadAllBytes($file.FullName)
                $text = [Text.UTF8Encoding]::new($false,$true).GetString($raw)
                $tail = $text.TrimEnd("`n").Split("`n")[-1] | ConvertFrom-Json
                $v = [double]$tail.last_call_end_seconds
                $current = [double][Diagnostics.Stopwatch]::GetTimestamp() / [Diagnostics.Stopwatch]::Frequency
                $valid = $raw.Length -gt 0 -and $raw.Length -le 5000000 -and $text.EndsWith("`n") -and
                    $tail.version -eq 'http_client_call_spacing_v1' -and $tail.pending -is [bool] -and !$tail.pending -and
                    ![double]::IsNaN($v) -and ![double]::IsInfinity($v) -and $v -ge 0 -and $v -le $current
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$tail.host_name)))).Replace('-','').ToLowerInvariant() }
                finally { $sha.Dispose() }
                $valid = $valid -and $file.Name -eq "$key.json"
            } catch { $issues += "Journal read/parse error: $($file.FullName): $($_.Exception.Message)" }
            if (!$valid) {
                $issues += "Invalid/pending journal: $($file.FullName)"
                Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $OpsDirectory ([Guid]::NewGuid().ToString('N') + '-' + $file.Name))
            }
            $journals += @{path=$file.FullName;valid=$valid;pending=$tail.pending;sha256=(Get-FileSha256 -Path $file.FullName)}
        }
    }
    $events = @()
    foreach ($filter in @(
        @{LogName='System';ProviderName='Microsoft-Windows-Kernel-General';Id=1;StartTime=$since},
        @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=42;StartTime=$since},
        @{LogName='System';ProviderName='Microsoft-Windows-Power-Troubleshooter';Id=1;StartTime=$since}
    )) {
        try { $events += @(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop | Select-Object RecordId,TimeCreated,ProviderName,Id,Message,@{n='xml';e={$_.ToXml()}}) }
        catch { if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $issues += "Event query unavailable: $($_.Exception.Message)" } }
    }
    if ($events.Count) { $issues += 'Clock-adjustment or sleep/wake event since preparation began' }
    $serviceCheckStatus = 'QUERY_FAILED'
    $service = Get-Service W32Time -ErrorAction Stop
    $serviceRunning = ($service.Status -eq 'Running')
    $serviceCheckStatus = 'OBSERVED'
    $svc = Get-CimInstance Win32_Service -Filter "Name='W32Time'" -ErrorAction Stop
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    if ($service.Status -ne 'Running') { $issues += 'W32Time is not Running' }
    $record = [ordered]@{
        schema_version='industry_audit_environment_v1';checked_at=(Get-Date -Format o)
        service_running=($service.Status -eq 'Running');service_process_id=$svc.ProcessId
        system_boot_time=$os.LastBootUpTime.ToString('o');timezone=[TimeZoneInfo]::Local.Id
        workers=$workers;tasks=$tasks;locks=$locks;journals=$journals;clock_or_power_events=$events;blockers=$issues
        controller_identity=$controllerDiagnostics
        controller_identity_version='protocol_controller_identity_v1'
        journal_changes=0;task_changes=0
    }
    if ($MinuteCapture) {
        $record.scope = 'minute_capture_environment_v1'
        $record.trade_date = $TradeDate
        $record.project_root = [IO.Path]::GetFullPath($ProjectRoot)
        $record.capture_root = [IO.Path]::GetFullPath($MinuteCaptureRoot)
        $record.cleanup_incomplete_exists = Test-Path -LiteralPath (Join-Path $MinuteCaptureRoot 'cleanup-incomplete.json')
        $record.date_output_exists = Test-Path -LiteralPath (Join-Path $MinuteCaptureRoot $TradeDate)
        if ($ProtocolDiagnostic) {
            $record.scope = 'protocol_diagnostic_environment_v1'
            $record.diagnostic_root = [IO.Path]::GetFullPath($ProtocolDiagnosticRoot)
            $record.shared_date_output_exists = $record.date_output_exists
            $record.date_output_exists = Test-Path -LiteralPath (Join-Path $ProtocolDiagnosticRoot $TradeDate)
            $record.cleanup_incomplete_exists = $record.cleanup_incomplete_exists -or
                (Test-Path -LiteralPath (Join-Path $ProtocolDiagnosticRoot 'cleanup-incomplete.json'))
        }
        $record.pending_preparations = $pendingPreparations
        $record.git_head = (& git -C $ProjectRoot rev-parse HEAD | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Git HEAD query failed' }
        $record.git_status = @(& git -C $ProjectRoot status --porcelain=v1)
        if ($LASTEXITCODE -ne 0) { throw 'Git status query failed' }
        # Keep native bytes: the Python verifier parses this same source, not a GO flag.
        $clockOut = Join-Path $OpsDirectory 'clock-status.stdout.bin'
        $clockErr = Join-Path $OpsDirectory 'clock-status.stderr.bin'
        if ((Test-Path -LiteralPath $clockOut) -or (Test-Path -LiteralPath $clockErr)) { throw 'Clock output already exists' }
        $clockTimer = [Diagnostics.Stopwatch]::StartNew()
        $clockProcess = Start-Process -FilePath 'w32tm.exe' -ArgumentList '/query','/status','/verbose' `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput $clockOut -RedirectStandardError $clockErr
        try {
            # PS5.1 Start-Process does not retain the native handle until requested.
            $null = $clockProcess.Handle
            $waitMs = [int][Math]::Max(0,4500 - $clockTimer.Elapsed.TotalMilliseconds)
            if (!$clockProcess.WaitForExit($waitMs)) {
                $clockProcess.Kill()
                $cleanupMs = [int][Math]::Max(0,5000 - $clockTimer.Elapsed.TotalMilliseconds)
                $reaped = $clockProcess.WaitForExit($cleanupMs)
                throw "Clock status query timed out; cleanup_complete=$reaped"
            }
            $clockExitCode = $clockProcess.ExitCode
            if ($null -eq $clockExitCode) { throw 'Clock status query exit code unavailable' }
            $record.clock_query = @{command=@('w32tm','/query','/status','/verbose');exit_code=$clockExitCode
                stdout_base64=[Convert]::ToBase64String([IO.File]::ReadAllBytes($clockOut))
                stderr_base64=[Convert]::ToBase64String([IO.File]::ReadAllBytes($clockErr))}
            if ($clockTimer.Elapsed.TotalMilliseconds -gt 5000) { throw 'Clock status query budget exceeded' }
        } finally { $clockProcess.Dispose() }
        $record.checked_at = Get-Date -Format o
    }
    Write-Result $OutputPath $record
    if ($MinuteCapture) { @{ok=(!$issues.Count);payload=$record} | ConvertTo-Json -Depth 20 }
    else { [ordered]@{status=if($issues.Count){'ENVIRONMENT_REJECTED'}else{'ENVIRONMENT_PASSED'};blockers=$issues} | ConvertTo-Json -Depth 5 }
    if (!$issues.Count) { $code = 0 }
} catch {
    $failure = [ordered]@{schema_version='industry_audit_environment_v1';status='ENVIRONMENT_INSPECTION_FAILED'
        blockers=@($_.Exception.Message);inspection_succeeded=$false
        service_running=$serviceRunning;service_check_status=$serviceCheckStatus;controller_identity=$controllerDiagnostics
        controller_identity_version='protocol_controller_identity_v1'}
    if (!(Test-Path -LiteralPath $OutputPath)) { Write-Result $OutputPath $failure }
    $failure | ConvertTo-Json -Depth 5
}
# This script is executed as a separate native process; success is never inherited.
exit $code
