[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [string]$Codes = '000001,000333,600000,600519,601318',

    [string]$ProjectRoot = '',

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot '..\..')
    )
}

$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
}
else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) { '' } else { $pythonCommand.Source }
}
$probeScript = Join-Path $ProjectRoot 'overnight_quant\scripts\run_minute_label_probe.py'
$stateMachineScript = Join-Path `
    $ProjectRoot `
    'overnight_quant\scripts\minute_probe_watchdog_state.ps1'
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

if (-not (Test-Path -LiteralPath $stateMachineScript -PathType Leaf)) {
    throw "state_machine_script_missing:$stateMachineScript"
}
. $stateMachineScript

$sourceStates = @{}
foreach ($source in $sources) {
    $sourceStates[$source] = New-WatchdogSourceState -Source $source
}

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
    return "pid=$($process.Id)"
}

function Get-ProbeTaskName {
    param([string]$Source)
    $label = if ($Source -eq 'mootdx') { 'Mootdx' } else { 'Eastmoney' }
    return "AStockMinuteProbe${label}-${compactDate}"
}

function Test-ProbeOutputExists {
    param([string]$Source)
    return Test-Path -LiteralPath (Get-OutputPath $Source) -PathType Leaf
}

function Test-ProbeCanStart {
    param([string]$Source)
    return [datetime]::Now -le $lastSafeStart
}

function Test-ProbeScheduledTaskExists {
    param([string]$Source)
    $taskName = Get-ProbeTaskName $Source
    return $null -ne (
        Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    )
}

function Start-ProbeScheduledTask {
    param([string]$Source)
    Start-ScheduledTask -TaskName (Get-ProbeTaskName $Source)
}

function Test-ProbeScheduledTaskRunning {
    param([string]$Source)
    $task = Get-ScheduledTask `
        -TaskName (Get-ProbeTaskName $Source) `
        -ErrorAction SilentlyContinue
    return $null -ne $task -and $task.State -eq 'Running'
}

function Wait-ProbeScheduledStart {
    param([string]$Source)
    Start-Sleep -Seconds 2
}

function Write-ProbeStateEvent {
    param(
        [string]$Source,
        [string]$Action,
        [string]$Detail
    )
    if ($Action -like 'terminal_output_*') {
        $existing = Get-ProbeResult (Get-OutputPath $Source)
        $Detail = if ($null -ne $existing) {
            [string]$existing.status
        }
        else {
            'UNREADABLE_OUTPUT'
        }
    }
    elseif ($Action -like 'scheduled_task_*') {
        $taskName = Get-ProbeTaskName $Source
        $Detail = if ([string]::IsNullOrWhiteSpace($Detail)) {
            $taskName
        }
        else {
            "$taskName|$Detail"
        }
    }
    Add-WatchdogEvent $Source $Action $Detail
}

function Ensure-ProbeRunning {
    param([string]$Source)
    Invoke-WatchdogSourceStep `
        -State $sourceStates[$Source] `
        -OutputExists ${function:Test-ProbeOutputExists} `
        -ProcessRunning ${function:Test-ProbeProcessRunning} `
        -CanStart ${function:Test-ProbeCanStart} `
        -ScheduledTaskExists ${function:Test-ProbeScheduledTaskExists} `
        -StartScheduledTask ${function:Start-ProbeScheduledTask} `
        -ScheduledTaskRunning ${function:Test-ProbeScheduledTaskRunning} `
        -AfterScheduledStart ${function:Wait-ProbeScheduledStart} `
        -StartDirect ${function:Start-DirectProbe} `
        -RecordEvent ${function:Write-ProbeStateEvent} |
        Out-Null
}

function Write-WatchdogResult {
    $sourceResults = [ordered]@{}
    $allPresent = $true
    foreach ($source in $sources) {
        $output = Get-OutputPath $source
        $filePresent = Test-Path -LiteralPath $output -PathType Leaf
        $result = Get-ProbeResult $output
        $present = $null -ne $result
        $sampleCount = if ($present) { @($result.samples).Count } else { 0 }
        $sourceResults[$source] = [ordered]@{
            output_present = $filePresent
            output_readable = $present
            status = if ($present) {
                [string]$result.status
            }
            elseif ($filePresent) {
                'UNREADABLE_OUTPUT'
            }
            else {
                'MISSING'
            }
            sample_count = $sampleCount
            probe_evidence_hash = if ($present) {
                [string]$result.probe_evidence_hash
            }
            else { '' }
            candidates = if ($present) { @($result.candidates).Count } else { 0 }
            tickets = if ($present) { @($result.tickets).Count } else { 0 }
            orders = if ($present) { @($result.orders).Count } else { 0 }
            source_state = $sourceStates[$source]
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

if ([string]::IsNullOrWhiteSpace($python) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
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
