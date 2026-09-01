[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        'scheduled_fast_complete',
        'scheduled_running',
        'scheduled_quick_exit',
        'scheduled_start_failure',
        'scheduled_missing',
        'adopted_process_exit',
        'existing_valid_output',
        'existing_unreadable_output'
    )]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [string]$StateMachineScript,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. $StateMachineScript

function Write-HarnessTextFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    Set-Content `
        -LiteralPath $Path `
        -Value $Content `
        -Encoding UTF8 `
        -NoNewline `
        -Force
}

function Write-HarnessBinaryFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [byte[]]$Content
    )

    Set-Content `
        -LiteralPath $Path `
        -Value $Content `
        -Encoding Byte `
        -Force
}

function Get-HarnessSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = [System.IO.File]::OpenRead($Path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $hasher.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace('-', '')
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

$parent = Split-Path -Parent $OutputPath
[IO.Directory]::CreateDirectory($parent) | Out-Null
if ($Scenario -eq 'existing_valid_output') {
    Write-HarnessTextFile `
        -Path $OutputPath `
        -Content '{"status":"TERMINAL"}'
}
elseif ($Scenario -eq 'existing_unreadable_output') {
    Write-HarnessBinaryFile `
        -Path $OutputPath `
        -Content ([byte[]](0xff, 0xfe, 0xfd))
}

$initialHash = if (Test-Path -LiteralPath $OutputPath) {
    Get-HarnessSha256 -Path $OutputPath
}
else {
    ''
}
$script:hashAfterScheduledWrite = ''
$script:scheduledStartCount = 0
$script:directStartCount = 0
$script:processCheckCount = 0
$script:scheduledRunningCheckCount = 0
$events = [Collections.Generic.List[object]]::new()
$state = New-WatchdogSourceState -Source 'mootdx'

$outputExists = {
    param([string]$Source)
    return Test-Path -LiteralPath $OutputPath -PathType Leaf
}
$processRunning = {
    param([string]$Source)
    $script:processCheckCount += 1
    if ($Scenario -eq 'adopted_process_exit') {
        return $script:processCheckCount -eq 1
    }
    return $false
}
$canStart = {
    param([string]$Source)
    return $true
}
$scheduledTaskExists = {
    param([string]$Source)
    return $Scenario -in @(
        'scheduled_fast_complete',
        'scheduled_running',
        'scheduled_quick_exit',
        'scheduled_start_failure'
    )
}
$startScheduledTask = {
    param([string]$Source)
    $script:scheduledStartCount += 1
    if ($Scenario -eq 'scheduled_start_failure') {
        throw 'simulated_scheduled_start_failure'
    }
}
$scheduledTaskRunning = {
    param([string]$Source)
    $script:scheduledRunningCheckCount += 1
    return $Scenario -eq 'scheduled_running'
}
$afterScheduledStart = {
    param([string]$Source)
    if ($Scenario -eq 'scheduled_fast_complete') {
        Write-HarnessTextFile `
            -Path $OutputPath `
            -Content '{"status":"SCHEDULED_TERMINAL"}'
        $script:hashAfterScheduledWrite = Get-HarnessSha256 `
            -Path $OutputPath
    }
}
$startDirect = {
    param([string]$Source)
    $script:directStartCount += 1
    Write-HarnessTextFile `
        -Path $OutputPath `
        -Content '{"status":"DIRECT_TERMINAL"}'
    return 'pid=1234'
}
$recordEvent = {
    param(
        [string]$Source,
        [string]$Action,
        [string]$Detail
    )
    $events.Add([ordered]@{
        source = $Source
        action = $Action
        detail = $Detail
    })
}

1..6 | ForEach-Object {
    Invoke-WatchdogSourceStep `
        -State $state `
        -OutputExists $outputExists `
        -ProcessRunning $processRunning `
        -CanStart $canStart `
        -ScheduledTaskExists $scheduledTaskExists `
        -StartScheduledTask $startScheduledTask `
        -ScheduledTaskRunning $scheduledTaskRunning `
        -AfterScheduledStart $afterScheduledStart `
        -StartDirect $startDirect `
        -RecordEvent $recordEvent
}

$finalHash = if (Test-Path -LiteralPath $OutputPath) {
    Get-HarnessSha256 -Path $OutputPath
}
else {
    ''
}

[ordered]@{
    scenario = $Scenario
    state = $state
    scheduled_start_count = $script:scheduledStartCount
    direct_start_count = $script:directStartCount
    process_check_count = $script:processCheckCount
    scheduled_running_check_count = $script:scheduledRunningCheckCount
    initial_hash = $initialHash
    hash_after_scheduled_write = $script:hashAfterScheduledWrite
    final_hash = $finalHash
    events = @($events)
    data_ready = $false
    candidates = @()
    tickets = @()
    orders = @()
} | ConvertTo-Json -Depth 10
