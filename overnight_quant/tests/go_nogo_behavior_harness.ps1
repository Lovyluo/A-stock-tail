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
    [string]$Date
)

$global:GoNoGoHarnessState = [ordered]@{
    state = 'Ready'
    disable_count = 0
    enable_count = 0
}

function global:Write-GoNoGoHarnessState {
    $json = ($global:GoNoGoHarnessState | ConvertTo-Json -Depth 4) + "`n"
    [IO.File]::WriteAllText(
        $StatePath,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
}

function global:Get-ScheduledTask {
    param(
        [string]$TaskName,
        $ErrorAction
    )
    if ($TaskName -ne 'UnitMainTask') {
        return $null
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
                Arguments = "-ProjectRoot `"$ProjectRoot`""
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
    return @()
}

function global:Get-TimeZone {
    return [pscustomobject]@{ Id = 'China Standard Time' }
}

function global:w32tm {
    param([Parameter(ValueFromRemainingArguments = $true)]$Arguments)
    $global:LASTEXITCODE = 0
}

Write-GoNoGoHarnessState
& $GoNoGoScript `
    -Date $Date `
    -MainRoot $ProjectRoot `
    -MainHead ('0' * 40) `
    -MainTaskName 'UnitMainTask' `
    -Endpoint '127.0.0.1:1' `
    -DeadlineClock '23:59:59' `
    -OutputDirectory $OutputDirectory
$goNoGoExitCode = $LASTEXITCODE
exit $goNoGoExitCode
