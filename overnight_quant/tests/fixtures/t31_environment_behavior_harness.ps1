param([string]$Script,[string]$OpsDirectory,[string]$OutputPath,[string]$TempDirectory,[string]$Mode)
$ErrorActionPreference = 'Stop'
$env:TEMP = $TempDirectory
$env:TMP = $TempDirectory
$global:LASTEXITCODE = 17
function global:Get-CimInstance {
    [CmdletBinding()] param([string]$ClassName,[string]$Filter)
    if ($ClassName -eq 'Win32_Service') { return [pscustomobject]@{ProcessId=42} }
    if ($ClassName -eq 'Win32_OperatingSystem') { return [pscustomobject]@{LastBootUpTime=[datetime]::Parse('2026-10-15T04:00:00+08:00')} }
    return
}
function global:Get-ScheduledTask {
    [CmdletBinding()] param()
    if ($Mode -eq 'denied') { throw [UnauthorizedAccessException]::new('synthetic task inventory denied') }
    if ($Mode -eq 'disabled') {
        return [pscustomobject]@{TaskPath='\';TaskName='AStockHistoricalDisabled';State='Disabled';
            Settings=[pscustomobject]@{Enabled=$false};Actions=@()}
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
    return [pscustomobject]@{Status='Running'}
}
# Only this isolated process has mocks. No real service, task, network or clock calls.
& $Script -OpsDirectory $OpsDirectory -OutputPath $OutputPath
exit $LASTEXITCODE
