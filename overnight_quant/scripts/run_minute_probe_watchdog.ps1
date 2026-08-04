[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [string]$Codes = '000001,000333,600000,600519,601318',

    [string]$ProjectRoot = 'D:\A-stock',

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$probeScript = Join-Path $ProjectRoot 'overnight_quant\scripts\run_minute_label_probe.py'
$cacheDir = Join-Path $ProjectRoot 'overnight_quant\data\cache'
$day = [datetime]::ParseExact(
    $Date,
    'yyyy-MM-dd',
    [Globalization.CultureInfo]::InvariantCulture
)
$startAt = $day.AddHours(14).AddMinutes(40)
$lastSafeStart = $day.AddHours(14).AddMinutes(49).AddSeconds(50)
$finishAt = $day.AddHours(14).AddMinutes(52).AddSeconds(30)
$compactDate = $Date.Replace('-', '')
$sources = @('mootdx', 'eastmoney')
$events = [Collections.Generic.List[object]]::new()
$directProcesses = @{}

function Add-WatchdogEvent {
    param(
        [string]$Source,
        [string]$Action,
        [string]$Detail = ''
    )
    $events.Add([ordered]@{
        at = [datetime]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        source = $Source
        action = $Action
        detail = $Detail
    })
}

function Get-OutputPath {
    param([string]$Source)
    return Join-Path $cacheDir "minute_label_probe_${Source}_${Date}.json"
}

function Get-ProbeResult {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        $bytes = [IO.File]::ReadAllBytes($Path)
        $encoding = [Text.UTF8Encoding]::new($false, $true)
        $content = $encoding.GetString($bytes)
        return $content | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-ProbeProcessRunning {
    param([string]$Source)
    $needle = "--source $Source"
    $dateNeedle = "--date $Date"
    return [bool](
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -like '*run_minute_label_probe.py*' -and
                $_.CommandLine -like "*$needle*" -and
                $_.CommandLine -like "*$dateNeedle*"
            } |
            Select-Object -First 1
    )
}

function Start-DirectProbe {
    param([string]$Source)
    $output = Get-OutputPath $Source
    $arguments = @(
        $probeScript,
        '--source', $Source,
        '--codes', $Codes,
        '--date', $Date,
        '--output', $output
    )
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru
    $directProcesses[$Source] = $process.Id
    Add-WatchdogEvent $Source 'direct_fallback_started' "pid=$($process.Id)"
}

function Ensure-ProbeRunning {
    param([string]$Source)
    $output = Get-OutputPath $Source
    $existing = Get-ProbeResult $output
    if ($null -ne $existing -and @($existing.samples).Count -eq 4) {
        Add-WatchdogEvent $Source 'valid_output_already_present' ([string]$existing.status)
        return
    }
    if (Test-ProbeProcessRunning $Source) {
        return
    }
    if ([datetime]::Now -gt $lastSafeStart) {
        Add-WatchdogEvent $Source 'late_restart_blocked' 'past_14:49:50'
        return
    }

    $label = if ($Source -eq 'mootdx') { 'Mootdx' } else { 'Eastmoney' }
    $taskName = "AStockMinuteProbe${label}-${compactDate}"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 2
        if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running' -or
            (Test-ProbeProcessRunning $Source)) {
            Add-WatchdogEvent $Source 'scheduled_task_started' $taskName
            return
        }
        Add-WatchdogEvent $Source 'scheduled_task_not_running' $taskName
    }
    else {
        Add-WatchdogEvent $Source 'scheduled_task_missing' $taskName
    }
    Start-DirectProbe $Source
}

function Write-WatchdogResult {
    $sourceResults = [ordered]@{}
    $allPresent = $true
    foreach ($source in $sources) {
        $path = Get-OutputPath $source
        $result = Get-ProbeResult $path
        $present = $null -ne $result
        $sampleCount = if ($present) { @($result.samples).Count } else { 0 }
        $sourceResults[$source] = [ordered]@{
            output_present = $present
            status = if ($present) { [string]$result.status } else { 'MISSING' }
            sample_count = $sampleCount
            probe_evidence_hash = if ($present) {
                [string]$result.probe_evidence_hash
            }
            else { '' }
            candidates = if ($present) { @($result.candidates).Count } else { 0 }
            tickets = if ($present) { @($result.tickets).Count } else { 0 }
            orders = if ($present) { @($result.orders).Count } else { 0 }
        }
        if (-not $present -or $sampleCount -ne 4) {
            $allPresent = $false
        }
    }
    $payload = [ordered]@{
        status = if ($allPresent) { 'WATCHDOG_COMPLETE' } else { 'WATCHDOG_INCOMPLETE' }
        execution_ok = $true
        data_ready = $false
        trade_date = $Date
        started_at = $script:watchdogStartedAt
        completed_at = [datetime]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        start_at = $startAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        last_safe_start = $lastSafeStart.ToString('yyyy-MM-ddTHH:mm:ssK')
        sources = $sourceResults
        events = @($events)
        candidates = @()
        tickets = @()
        orders = @()
    }
    $path = Join-Path $cacheDir "minute_probe_watchdog_${Date}.json"
    $temporary = "$path.$PID.tmp"
    $json = ($payload | ConvertTo-Json -Depth 10) + "`n"
    [IO.File]::WriteAllText(
        $temporary,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
    if (Test-Path -LiteralPath $path) {
        [IO.File]::Replace($temporary, $path, $null)
    }
    else {
        [IO.File]::Move($temporary, $path)
    }
    return $payload
}

$script:watchdogStartedAt = [datetime]::Now.ToString(
    'yyyy-MM-ddTHH:mm:ss.fffK'
)

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "python_missing:$python"
}
if (-not (Test-Path -LiteralPath $probeScript -PathType Leaf)) {
    throw "probe_script_missing:$probeScript"
}
if (-not (Test-Path -LiteralPath $cacheDir -PathType Container)) {
    throw "cache_directory_missing:$cacheDir"
}

if ($ValidateOnly) {
    [ordered]@{
        status = 'WATCHDOG_VALIDATED'
        trade_date = $Date
        start_at = $startAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        last_safe_start = $lastSafeStart.ToString('yyyy-MM-ddTHH:mm:ssK')
        finish_at = $finishAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        sources = $sources
        candidates = @()
        tickets = @()
        orders = @()
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ([datetime]::Now.Date -ne $day.Date -or [datetime]::Now -gt $finishAt) {
    Add-WatchdogEvent '' 'watchdog_window_missed' $Date
    $result = Write-WatchdogResult
    $result | ConvertTo-Json -Depth 10
    exit 2
}

while ([datetime]::Now -lt $finishAt) {
    $now = [datetime]::Now
    if ($now -ge $startAt -and $now -le $lastSafeStart) {
        foreach ($source in $sources) {
            Ensure-ProbeRunning $source
        }
    }
    Start-Sleep -Seconds 5
}

$finalResult = Write-WatchdogResult
$finalResult | ConvertTo-Json -Depth 10
exit $(if ($finalResult.status -eq 'WATCHDOG_COMPLETE') { 0 } else { 2 })
