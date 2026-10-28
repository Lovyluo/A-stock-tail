param([string]$Script,[string]$OpsDirectory,[string]$OutputPath,[string]$TempDirectory,[string]$Mode,
    [switch]$MinuteCapture,[string]$ProjectRoot,[string]$MinuteCaptureRoot,[string]$TradeDate,
    [switch]$ProtocolDiagnostic,[string]$ProtocolDiagnosticRoot,[int]$AllowedControllerPid=0,
    [string]$ControllerPythonPath,[string]$ControllerBasePythonPath,[string]$ControllerWorkingDirectory,
    [string]$ProcessFault,[string]$SyntheticHead,[string]$SyntheticCheckedAt,[string]$SyntheticBootTime,
    [switch]$HashUnavailable,[string]$HashTarget,[string]$ClockExecutable,[string]$ClockArguments)
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
    if ($ClassName -eq 'Win32_OperatingSystem') { return [pscustomobject]@{LastBootUpTime=[datetime]::Parse($(if($SyntheticBootTime){$SyntheticBootTime}else{'2026-10-15T04:00:00+08:00'}))} }
    if ($MinuteCapture -and $ClassName -eq 'Win32_Process') {
        if ($Mode -eq 'real-processes') {
            if ($ProcessFault -eq 'query-error') { throw 'synthetic process query denied' }
            $rows = @(CimCmdlets\Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine)
            $target = @($rows | Where-Object { $_.ProcessId -eq $AllowedControllerPid })
            if ($target.Count -eq 1) {
                if ($ProcessFault -eq 'missing-command') { $target[0].CommandLine=$null }
                if ($ProcessFault -eq 'wrong-image') { $target[0].ExecutablePath='C:\unapproved\python.exe' }
                if ($ProcessFault -eq 'wrong-name') { $target[0].Name='unapproved.exe';$target[0].ExecutablePath='C:\unapproved\unapproved.exe' }
                if ($ProcessFault -eq 'wrong-entry') { $target[0].CommandLine='python.exe C:\unapproved\run_minute_protocol_diagnostic.py --network --secret NEVER_SAVE_THIS' }
                if ($ProcessFault -eq 'wrong-parent') { $target[0].ParentProcessId=2147483647 }
                if ($ProcessFault -eq 'missing-image') { $target[0].ExecutablePath=$null }
                $parent = @($rows | Where-Object { $_.ProcessId -eq $target[0].ParentProcessId })
                if ($parent.Count -eq 1 -and $ProcessFault -eq 'parent-command-missing') { $parent[0].CommandLine=$null }
                if ($parent.Count -eq 1 -and $ProcessFault -eq 'ancestor-mismatch') {
                    # Explicit negative model, even when the real CI launch parent is PowerShell.
                    $parent[0].Name='python.exe'
                    $parent[0].ExecutablePath=$ControllerPythonPath
                    $parent[0].CommandLine=$target[0].CommandLine+' --secret NEVER_SAVE_THIS'
                }
                if ($parent.Count -eq 1 -and $ProcessFault -eq 'worker-parent') { $parent[0].CommandLine='python -m overnight_quant.data.minute_protocol_worker' }
            }
            $chain = @(); $next = $AllowedControllerPid
            for ($i=0; $i -lt 6 -and $next -gt 0; $i++) {
                $row = @($rows | Where-Object { $_.ProcessId -eq $next })
                if ($row.Count -ne 1) { break }
                $chain += $row[0] | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine
                $next = [int]$row[0].ParentProcessId
            }
            @{test_only=$true;chain=$chain} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OpsDirectory 'synthetic-process-chain.json') -Encoding UTF8
            return $rows
        }
        if ($Mode -in @('worker','cli','quoted-cli','module-cli','module-cli-compact','prepared-worker','prepared-cli','prepared-module','protocol-worker','protocol-differential-worker','protocol-cli','protocol-controller-worker')) {
            $command = if($Mode -eq 'worker') {'python -m overnight_quant.data.minute_capture_worker'} `
                elseif($Mode -eq 'prepared-worker') {'python -m overnight_quant.data.minute_local_prepare_worker'} `
                elseif($Mode -eq 'prepared-cli') {'python overnight_quant/scripts/run_minute_local_prepare_audit.py --network'} `
                elseif($Mode -eq 'prepared-module') {'python -movernight_quant.scripts.run_minute_local_prepare_audit --network'} `
                elseif($Mode -eq 'protocol-worker') {'python -m overnight_quant.data.minute_protocol_worker'} `
                elseif($Mode -eq 'protocol-differential-worker') {'python -m overnight_quant.data.minute_protocol_differential_worker'} `
                elseif($Mode -in @('protocol-cli','protocol-controller-worker')) {'python -m overnight_quant.scripts.run_minute_protocol_diagnostic --network'} `
                elseif($Mode -eq 'cli') {'python overnight_quant/scripts/run_minute_capture_audit.py --network'} `
                elseif($Mode -eq 'module-cli') {'python -m overnight_quant.scripts.run_minute_capture_audit --network'} `
                elseif($Mode -eq 'module-cli-compact') {'python -movernight_quant.scripts.run_minute_capture_audit --network'} `
                else {'python "C:\synthetic directory\run_minute_capture_audit.py" --network'}
            [pscustomobject]@{ProcessId=6000;ParentProcessId=$(if($AllowedControllerPid){5999}else{1});Name='python.exe';CommandLine=$command;ExecutablePath='C:\synthetic\python.exe'}
            if ($AllowedControllerPid) {
                [pscustomobject]@{ProcessId=5999;ParentProcessId=1;Name='powershell.exe';ExecutablePath='C:\synthetic\powershell.exe';CommandLine='powershell.exe -Command python -m overnight_quant.scripts.run_minute_protocol_diagnostic --network'}
            }
            [pscustomobject]@{ProcessId=6001;ParentProcessId=6000;Name='synthetic-helper.exe';CommandLine='test-only child'}
            if ($Mode -eq 'protocol-controller-worker') {
                [pscustomobject]@{ProcessId=6010;ParentProcessId=6000;Name='python.exe';CommandLine='python -m overnight_quant.data.minute_protocol_worker'}
                [pscustomobject]@{ProcessId=6011;ParentProcessId=6010;Name='synthetic-helper.exe';CommandLine='test-only child'}
            }
        }
        [pscustomobject]@{ProcessId=6002;ParentProcessId=1;Name='python.exe';CommandLine='python unrelated_synthetic_app.py'}
        [pscustomobject]@{ProcessId=$PID;ParentProcessId=$AllowedControllerPid;Name='powershell.exe';ExecutablePath='C:\synthetic\powershell.exe';CommandLine='powershell.exe -File inspector.ps1'}
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
    if ($Mode -in @('minute-task','minute-task-disabled','prepared-task','protocol-task')) {
        return [pscustomobject]@{TaskPath='\';TaskName=$(if($Mode -in @('prepared-task','protocol-task')){'OrdinaryResearch'}else{'DailyMinuteCapture'});
            State=$(if($Mode -in @('minute-task','protocol-task')){'Ready'}else{'Disabled'});
            Settings=[pscustomobject]@{Enabled=($Mode -in @('minute-task','prepared-task','protocol-task'))};
            Actions=@([pscustomobject]@{Execute='python.exe';
                Arguments=$(if($Mode -eq 'prepared-task'){'-m overnight_quant.scripts.run_minute_local_prepare_audit'}elseif($Mode -eq 'protocol-task'){'-m overnight_quant.scripts.run_minute_protocol_diagnostic'}else{'-m overnight_quant.scripts.run_minute_capture_audit'});
                WorkingDirectory=''})}
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
        if ($args -contains 'rev-parse') { return $(if($SyntheticHead){$SyntheticHead}else{('a' * 40)}) }
    }
    function global:Start-Process {
        param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
        if ($FilePath -ne 'w32tm.exe' -or ($ArgumentList -join ' ') -ne '/query /status /verbose') {
            throw 'Unexpected real process request in isolated harness'
        }
        if ($ClockExecutable) {
            return Microsoft.PowerShell.Management\Start-Process -FilePath $ClockExecutable -ArgumentList $ClockArguments `
                -WindowStyle Hidden -PassThru -RedirectStandardOutput $RedirectStandardOutput -RedirectStandardError $RedirectStandardError
        }
        $text = "Leap Indicator: 0`nLast Successful Sync Time: 2026/10/21 14:30:00`nSource: time.windows.com,0x9`nLast Sync Error: 0`n"
        [IO.File]::WriteAllBytes($RedirectStandardOutput,[Text.Encoding]::UTF8.GetBytes($text))
        [IO.File]::WriteAllBytes($RedirectStandardError,[byte[]]@())
        $fake = [pscustomobject]@{ExitCode=$(if($Mode -eq 'clock-null'){$null}else{0});Handle=[IntPtr]1}
        $fake | Add-Member ScriptMethod WaitForExit {param($Milliseconds) return $true}
        $fake | Add-Member ScriptMethod Kill {}
        $fake | Add-Member ScriptMethod Refresh {}
        $fake | Add-Member ScriptMethod Dispose {}
        return $fake
    }
}
if ($SyntheticCheckedAt) {
    function global:Get-Date { param([string]$Format) return $SyntheticCheckedAt }
    function global:Get-TimeZone { return [pscustomobject]@{Id='China Standard Time'} }
}
# Only this isolated process has mocks. No real service, task, network or clock calls.
$heldJournal = $null
try {
    if ($Mode -eq 'journal-read-failure') {
        $heldJournal = [IO.File]::Open($HashTarget,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
    }
    if ($MinuteCapture) {
        & $Script -OpsDirectory $OpsDirectory -OutputPath $OutputPath -MinuteCapture `
            -ProjectRoot $ProjectRoot -MinuteCaptureRoot $MinuteCaptureRoot -TradeDate $TradeDate `
            -ProtocolDiagnostic:$ProtocolDiagnostic -ProtocolDiagnosticRoot $ProtocolDiagnosticRoot -AllowedControllerPid $AllowedControllerPid `
            -ControllerPythonPath $ControllerPythonPath -ControllerBasePythonPath $ControllerBasePythonPath `
            -ControllerWorkingDirectory $ControllerWorkingDirectory
    } else { & $Script -OpsDirectory $OpsDirectory -OutputPath $OutputPath }
} finally { if ($null -ne $heldJournal) { $heldJournal.Dispose() } }
exit $LASTEXITCODE
