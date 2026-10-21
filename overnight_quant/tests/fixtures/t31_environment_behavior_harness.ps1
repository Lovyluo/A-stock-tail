param([string]$Script,[string]$OpsDirectory,[string]$OutputPath,[string]$TempDirectory,[string]$Mode,
    [switch]$MinuteCapture,[string]$ProjectRoot,[string]$MinuteCaptureRoot,[string]$TradeDate,
    [switch]$HashUnavailable,[string]$HashTarget)
$ErrorActionPreference = 'Stop'
$env:TEMP = $TempDirectory
$env:TMP = $TempDirectory
$global:LASTEXITCODE = 17
if ($HashUnavailable) {
    function global:Get-FileHash {
        throw [Management.Automation.CommandNotFoundException]::new('test-only Get-FileHash unavailable')
    }
}
if ($Mode -in @('hash-file','hash-locked')) {
    $locked = $null
    try {
        $tokens = $null; $parseErrors = $null
        $tree = [Management.Automation.Language.Parser]::ParseFile($Script,[ref]$tokens,[ref]$parseErrors)
        if ($parseErrors.Count) { throw 'Synthetic hash target script parse failed' }
        $function = $tree.Find({param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-FileSha256'
        },$false)
        if (!$function) { throw 'Production file hash function missing' }
        . ([scriptblock]::Create($function.Extent.Text))
        if ($Mode -eq 'hash-locked') {
            $locked = [IO.File]::Open($HashTarget,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        }
        $digest = Get-FileSha256 -Path $HashTarget
        $exclusive = [IO.File]::Open($HashTarget,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        $exclusive.Dispose()
        @{test_only=$true;sha256=$digest;handles_released=$true} | ConvertTo-Json
        exit 0
    } catch {
        @{test_only=$true;error=$_.Exception.Message} | ConvertTo-Json
        exit 2
    } finally { if ($null -ne $locked) { $locked.Dispose() } }
}
function global:Get-CimInstance {
    [CmdletBinding()] param([string]$ClassName,[string]$Filter)
    if ($ClassName -eq 'Win32_Service') {
        if ($Mode -eq 'identity-query-error') { throw 'synthetic service identity denied' }
        return [pscustomobject]@{ProcessId=42}
    }
    if ($ClassName -eq 'Win32_OperatingSystem') { return [pscustomobject]@{LastBootUpTime=[datetime]::Parse('2026-10-15T04:00:00+08:00')} }
    if ($MinuteCapture -and $ClassName -eq 'Win32_Process') {
        if ($Mode -in @('worker','cli','quoted-cli','module-cli','module-cli-compact')) {
            $command = if($Mode -eq 'worker') {'python -m overnight_quant.data.minute_capture_worker'} `
                elseif($Mode -eq 'cli') {'python overnight_quant/scripts/run_minute_capture_audit.py --network'} `
                elseif($Mode -eq 'module-cli') {'python -m overnight_quant.scripts.run_minute_capture_audit --network'} `
                elseif($Mode -eq 'module-cli-compact') {'python -movernight_quant.scripts.run_minute_capture_audit --network'} `
                else {'python "C:\synthetic directory\run_minute_capture_audit.py" --network'}
            [pscustomobject]@{ProcessId=6000;ParentProcessId=1;Name='python.exe';CommandLine=$command}
            [pscustomobject]@{ProcessId=6001;ParentProcessId=6000;Name='synthetic-helper.exe';CommandLine='test-only child'}
        }
        [pscustomobject]@{ProcessId=6002;ParentProcessId=1;Name='python.exe';CommandLine='python unrelated_synthetic_app.py'}
    }
    return
}
function global:Get-ScheduledTask {
    [CmdletBinding()] param()
    if ($Mode -eq 'denied') { throw [UnauthorizedAccessException]::new('synthetic task inventory denied') }
    if ($Mode -eq 'disabled') {
        return [pscustomobject]@{TaskPath='\';TaskName='AStockHistoricalDisabled';State='Disabled';
            Settings=[pscustomobject]@{Enabled=$false};Actions=@()}
    }
    if ($Mode -in @('minute-task','minute-task-disabled')) {
        return [pscustomobject]@{TaskPath='\';TaskName='DailyMinuteCapture';
            State=$(if($Mode -eq 'minute-task'){'Ready'}else{'Disabled'});
            Settings=[pscustomobject]@{Enabled=($Mode -eq 'minute-task')};
            Actions=@([pscustomobject]@{Execute='python.exe';Arguments='-m overnight_quant.scripts.run_minute_capture_audit';WorkingDirectory=''})}
    }
}
function global:Get-WinEvent {
    [CmdletBinding()] param([hashtable]$FilterHashtable)
    $errorRecord = [Management.Automation.ErrorRecord]::new([InvalidOperationException]::new('synthetic no events'),
        'NoMatchingEventsFound',[Management.Automation.ErrorCategory]::ObjectNotFound,$null)
    $PSCmdlet.ThrowTerminatingError($errorRecord)
}
function global:Get-Service {
    [CmdletBinding()] param([string]$Name)
    if ($Mode -eq 'service-query-error') { throw 'synthetic service query denied' }
    if ($Mode -eq 'service-stopped') { return [pscustomobject]@{Status='Stopped'} }
    return [pscustomobject]@{Status='Running'}
}
if ($MinuteCapture) {
    function global:git {
        $global:LASTEXITCODE = 0
        if ($args -contains 'rev-parse') { return ('a' * 40) }
    }
    function global:Start-Process {
        param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
        if ($FilePath -ne 'w32tm.exe' -or ($ArgumentList -join ' ') -ne '/query /status /verbose') {
            throw 'Unexpected real process request in isolated harness'
        }
        $text = "Leap Indicator: 0`nLast Successful Sync Time: 2026/10/21 14:30:00`nSource: time.windows.com,0x9`nLast Sync Error: 0`n"
        [IO.File]::WriteAllBytes($RedirectStandardOutput,[Text.Encoding]::UTF8.GetBytes($text))
        [IO.File]::WriteAllBytes($RedirectStandardError,[byte[]]@())
        $fake = [pscustomobject]@{ExitCode=0}
        $fake | Add-Member ScriptMethod WaitForExit {param($Milliseconds) return $true}
        $fake | Add-Member ScriptMethod Kill {}
        $fake | Add-Member ScriptMethod Refresh {}
        $fake | Add-Member ScriptMethod Dispose {}
        return $fake
    }
}
# Only this isolated process has mocks. No real service, task, network or clock calls.
$heldJournal = $null
try {
    if ($Mode -eq 'journal-read-failure') {
        $heldJournal = [IO.File]::Open($HashTarget,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
    }
    if ($MinuteCapture) {
        & $Script -OpsDirectory $OpsDirectory -OutputPath $OutputPath -MinuteCapture `
            -ProjectRoot $ProjectRoot -MinuteCaptureRoot $MinuteCaptureRoot -TradeDate $TradeDate
    } else { & $Script -OpsDirectory $OpsDirectory -OutputPath $OutputPath }
} finally { if ($null -ne $heldJournal) { $heldJournal.Dispose() } }
exit $LASTEXITCODE
