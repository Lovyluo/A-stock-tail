param(
    [Parameter(Mandatory=$true)][string]$OpsDirectory,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$code = 2
function Write-Result($Path,$Value) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 15))
    $f = [IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $f.Write($bytes,0,$bytes.Length);$f.Flush($true) } finally { $f.Dispose() }
}
try {
    if (Test-Path -LiteralPath $OutputPath) { throw 'Immutable environment output exists' }
    $intent = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $OpsDirectory 'intent.json') | ConvertFrom-Json
    $since = [DateTimeOffset]::Parse([string]$intent.created_at).LocalDateTime
    $issues = @()
    $workers = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.Name -match '^(python|pythonw|powershell|pwsh|dotnet)(\.exe)?$' -and
        $_.CommandLine -match 'market_httpclient_worker\.ps1|market_source_worker\.py|run_complete_industry_audit\.py|run_market_source_validation\.py|pytest|run_minute_probe_watchdog\.ps1'
    } | Select-Object ProcessId,ParentProcessId,Name,CommandLine)
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
        $_.TaskName -match 'AStock|A-stock|MarketSource|IndustryAudit' -or
        (@($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' ') -match 'A-stock|market_source|complete_industry'
    } | ForEach-Object { [ordered]@{TaskPath=$_.TaskPath;TaskName=$_.TaskName;State=[string]$_.State;Enabled=[bool]$_.Settings.Enabled} })
    if ($workers.Count) { $issues += 'Residual worker present' }
    if (@($tasks | Where-Object { $_.Enabled -or $_.State -eq 'Running' }).Count) { $issues += 'Active project task present' }
    $locks = @()
    $journals = @()
    $temp = [IO.Path]::GetTempPath()
    foreach ($name in @('a-stock-t25-global-executor.lock','a-stock-t25-global-executor.stage')) {
        $path = Join-Path $temp $name
        if (Test-Path -LiteralPath $path) {
            $issues += "Executor lock exists: $path"
            $copy = Join-Path $OpsDirectory ([Guid]::NewGuid().ToString('N') + '-' + $name)
            Copy-Item -LiteralPath $path -Destination $copy
            $locks += @{path=$path;preserved_copy=$copy;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
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
            $journals += @{path=$file.FullName;valid=$valid;pending=$tail.pending;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
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
    $service = Get-Service W32Time -ErrorAction Stop
    $svc = Get-CimInstance Win32_Service -Filter "Name='W32Time'" -ErrorAction Stop
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    if ($service.Status -ne 'Running') { $issues += 'W32Time is not Running' }
    $record = [ordered]@{
        schema_version='industry_audit_environment_v1';checked_at=(Get-Date -Format o)
        service_running=($service.Status -eq 'Running');service_process_id=$svc.ProcessId
        system_boot_time=$os.LastBootUpTime.ToString('o');timezone=[TimeZoneInfo]::Local.Id
        workers=$workers;tasks=$tasks;locks=$locks;journals=$journals;clock_or_power_events=$events;blockers=$issues
        journal_changes=0;task_changes=0
    }
    Write-Result $OutputPath $record
    [ordered]@{status=if($issues.Count){'ENVIRONMENT_REJECTED'}else{'ENVIRONMENT_PASSED'};blockers=$issues} | ConvertTo-Json -Depth 5
    if (!$issues.Count) { $code = 0 }
} catch {
    $failure = [ordered]@{schema_version='industry_audit_environment_v1';status='ENVIRONMENT_INSPECTION_FAILED';blockers=@($_.Exception.Message);service_running=$false}
    if (!(Test-Path -LiteralPath $OutputPath)) { Write-Result $OutputPath $failure }
    $failure | ConvertTo-Json -Depth 5
}
# This script is executed as a separate native process; success is never inherited.
exit $code
