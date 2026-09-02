[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [string]$Codes = '000001,000333,600000,600519,601318',

    [string]$ProjectRoot = '',

    [string]$MootdxEndpoint = '',

    [string]$MootdxEndpointId = '',

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
$proxyReadinessScript = Join-Path `
    $ProjectRoot `
    'overnight_quant\scripts\source_proxy_readiness.ps1'
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
if (-not (Test-Path -LiteralPath $proxyReadinessScript -PathType Leaf)) {
    throw "proxy_readiness_script_missing:$proxyReadinessScript"
}
. $stateMachineScript
. $proxyReadinessScript

$sourceReadiness = [ordered]@{
    mootdx = Get-SourceProxyReadiness -Source 'mootdx'
    eastmoney = Get-SourceProxyReadiness -Source 'eastmoney'
}
$activeSources = @(
    $sources |
        Where-Object { $sourceReadiness[$_].source_allowed -eq $true }
)

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
    if ($Source -eq 'mootdx') {
        $arguments += @('--endpoint', $MootdxEndpoint)
        if (-not [string]::IsNullOrWhiteSpace($MootdxEndpointId)) {
            $arguments += @('--endpoint-id', $MootdxEndpointId)
        }
    }
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
    $task = Get-ScheduledTask `
        -TaskName $taskName `
        -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return $false
    }
    if ($Source -ne 'mootdx') {
        return $true
    }
    $arguments = [string](@($task.Actions)[0].Arguments)
    return (
        $arguments -like "*--endpoint $MootdxEndpoint*" -and
        (
            [string]::IsNullOrWhiteSpace($MootdxEndpointId) -or
            $arguments -like "*--endpoint-id $MootdxEndpointId*"
        )
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
        $sourceAllowed = $sourceReadiness[$source].source_allowed -eq $true
        $sourceResults[$source] = [ordered]@{
            output_present = $filePresent
            output_readable = $present
            status = if ($present) {
                [string]$result.status
            }
            elseif ($filePresent) {
                'UNREADABLE_OUTPUT'
            }
            elseif (-not $sourceAllowed) {
                [string]$sourceReadiness[$source].status
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
        if ($sourceAllowed -and (-not $present -or $sampleCount -ne 4)) {
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
        source_readiness = $sourceReadiness
        active_sources = $activeSources
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
$endpointText = ([string]$MootdxEndpoint).Trim()
$endpointSeparatorIndex = $endpointText.LastIndexOf(':')
$endpointHost = if ($endpointSeparatorIndex -gt 0) {
    $endpointText.Substring(0, $endpointSeparatorIndex).Trim()
}
else { '' }
$endpointPortText = if (
    $endpointSeparatorIndex -gt 0 -and
    $endpointSeparatorIndex -lt ($endpointText.Length - 1)
) {
    $endpointText.Substring($endpointSeparatorIndex + 1)
}
else { '' }
$endpointPort = 0
$fixedEndpointValid = (
    $endpointSeparatorIndex -gt 0 -and
    -not [string]::IsNullOrWhiteSpace($endpointHost) -and
    [int]::TryParse($endpointPortText, [ref]$endpointPort) -and
    $endpointPort -gt 0 -and
    $endpointPort -le 65535
)

if ($ValidateOnly) {
    [ordered]@{
        status = if ($fixedEndpointValid) {
            'WATCHDOG_VALIDATED'
        }
        else {
            'WATCHDOG_FIXED_ENDPOINT_REQUIRED'
        }
        trade_date = $Date
        start_at = $startAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        last_safe_start = $lastSafeStart.ToString('yyyy-MM-ddTHH:mm:ssK')
        finish_at = $finishAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        sources = $sources
        source_readiness = $sourceReadiness
        active_sources = $activeSources
        mootdx_endpoint = $MootdxEndpoint
        mootdx_endpoint_id = $MootdxEndpointId
        candidates = @()
        tickets = @()
        orders = @()
    } | ConvertTo-Json -Depth 4
    exit $(if ($fixedEndpointValid) { 0 } else { 2 })
}

if (-not $fixedEndpointValid) {
    throw 'mootdx_fixed_endpoint_required'
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
        foreach ($source in $activeSources) {
            Ensure-ProbeRunning $source
        }
    }
    Start-Sleep -Seconds 5
}

$finalResult = Write-WatchdogResult
$finalResult | ConvertTo-Json -Depth 10
exit $(if ($finalResult.status -eq 'WATCHDOG_COMPLETE') { 0 } else { 2 })
