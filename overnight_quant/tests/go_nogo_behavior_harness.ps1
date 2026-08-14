[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$GoNoGoScript,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$StatePath,

    [Parameter(Mandatory = $true)]
    [string]$Date,

    [ValidateSet(
        'gate_failure',
        'existing_result',
        'invalid_endpoint',
        'invalid_codes',
        'preflight_exception',
        'write_failure',
        'validate_only'
    )]
    [string]$Scenario = 'gate_failure'
)

$global:GoNoGoHarnessFormalCodes = (
    '000001,000333,600000,600519,601318'
)
$global:GoNoGoHarnessState = [ordered]@{
    state = 'Ready'
    disable_count = 0
    enable_count = 0
    scenario = $Scenario
    existing_hash_before = ''
    existing_hash_after = ''
    blocking_hash_before = ''
    blocking_hash_after = ''
}

function global:Write-GoNoGoHarnessState {
    $json = ($global:GoNoGoHarnessState | ConvertTo-Json -Depth 4) + "`n"
    [IO.File]::WriteAllText(
        $StatePath,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
}

function global:Get-GoNoGoHarnessSha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $hasher.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes)).Replace('-', '')
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function global:Get-ScheduledTask {
    param(
        [string]$TaskName,
        $ErrorAction
    )
    if ($TaskName -ne 'UnitMainTask') {
        return $null
    }
    $taskCodes = if ($Scenario -eq 'invalid_codes') {
        '000001'
    }
    else {
        $global:GoNoGoHarnessFormalCodes
    }
    return [pscustomobject]@{
        TaskName = $TaskName
        State = $global:GoNoGoHarnessState.state
        Settings = [pscustomobject]@{
            MultipleInstances = 'IgnoreNew'
            RestartCount = 0
            WakeToRun = $true
            StartWhenAvailable = $false
        }
        Actions = @(
            [pscustomobject]@{
                Arguments = (
                    "-ProjectRoot `"$ProjectRoot`" " +
                    "-Codes `"$taskCodes`""
                )
            }
        )
    }
}

function global:Disable-ScheduledTask {
    param([string]$TaskName)
    $global:GoNoGoHarnessState.state = 'Disabled'
    $global:GoNoGoHarnessState.disable_count += 1
    Write-GoNoGoHarnessState
    return Get-ScheduledTask -TaskName $TaskName
}

function global:Enable-ScheduledTask {
    param([string]$TaskName)
    $global:GoNoGoHarnessState.state = 'Ready'
    $global:GoNoGoHarnessState.enable_count += 1
    Write-GoNoGoHarnessState
    return Get-ScheduledTask -TaskName $TaskName
}

function global:Get-CimInstance {
    param(
        [string]$ClassName,
        $ErrorAction
    )
    if ($Scenario -eq 'preflight_exception') {
        throw 'unit_preflight_exception'
    }
    return @()
}

function global:Get-TimeZone {
    return [pscustomobject]@{ Id = 'China Standard Time' }
}

function global:w32tm {
    param([Parameter(ValueFromRemainingArguments = $true)]$Arguments)
    $global:LASTEXITCODE = 0
}

$effectiveOutput = $OutputDirectory
if ($Scenario -eq 'write_failure') {
    $effectiveOutput = Join-Path $OutputDirectory 'not-a-directory'
    [IO.File]::WriteAllText(
        $effectiveOutput,
        'blocking file',
        [Text.UTF8Encoding]::new($false)
    )
    $global:GoNoGoHarnessState.blocking_hash_before = (
        Get-GoNoGoHarnessSha256 $effectiveOutput
    )
}

$resultPath = Join-Path `
    $effectiveOutput `
    "minute_probe_go_nogo_${Date}.json"
if ($Scenario -eq 'existing_result') {
    [IO.Directory]::CreateDirectory($effectiveOutput) | Out-Null
    [IO.File]::WriteAllText(
        $resultPath,
        "{`"status`":`"SAMPLING_NO_GO`",`"immutable`":true}`n",
        [Text.UTF8Encoding]::new($false)
    )
    $global:GoNoGoHarnessState.existing_hash_before = (
        Get-GoNoGoHarnessSha256 $resultPath
    )
}

$actualHead = ((& git -C $ProjectRoot rev-parse HEAD) | Out-String).Trim()
$requestedHead = if ($Scenario -eq 'gate_failure') {
    '0' * 40
}
else {
    $actualHead
}
$requestedEndpoint = if ($Scenario -eq 'invalid_endpoint') {
    'invalid-endpoint'
}
else {
    '127.0.0.1:1'
}
$requestedCodes = if ($Scenario -eq 'invalid_codes') {
    '000001'
}
else {
    $global:GoNoGoHarnessFormalCodes
}

Write-GoNoGoHarnessState
$invokeParameters = @{
    Date = $Date
    MainRoot = $ProjectRoot
    MainHead = $requestedHead
    MainTaskName = 'UnitMainTask'
    Codes = $requestedCodes
    Endpoint = $requestedEndpoint
    DeadlineClock = '23:59:59'
    OutputDirectory = $effectiveOutput
}
if ($Scenario -eq 'validate_only') {
    $invokeParameters.ValidateOnly = $true
}
& $GoNoGoScript @invokeParameters
$goNoGoExitCode = $LASTEXITCODE

if ($Scenario -eq 'existing_result') {
    $global:GoNoGoHarnessState.existing_hash_after = (
        Get-GoNoGoHarnessSha256 $resultPath
    )
}
if ($Scenario -eq 'write_failure') {
    $global:GoNoGoHarnessState.blocking_hash_after = (
        Get-GoNoGoHarnessSha256 $effectiveOutput
    )
}
Write-GoNoGoHarnessState
exit $goNoGoExitCode
