[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [Parameter(Mandatory = $true)]
    [string]$MainRoot,

    [Parameter(Mandatory = $true)]
    [string]$MainHead,

    [Parameter(Mandatory = $true)]
    [string]$MainTaskName,

    [string]$AuditRoot = '',
    [string]$AuditHead = '',
    [string]$AuditTaskName = '',
    [string]$GuardTaskName = '',
    [string]$AuditCode = '600000',
    [string]$Codes = '000001,000333,600000,600519,601318',

    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [string]$EndpointId = '',
    [ValidateRange(1, 2000)]
    [int]$RequestDeadlineMs = 2000,
    [string]$DeadlineClock = '14:30:00',
    [string]$RecoveryDeadlineClock = '14:39:30',

    [ValidateSet('standard', 'recovery')]
    [string]$RecoveryMode = 'standard',

    [string]$OutputDirectory = '',
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$isRecovery = $RecoveryMode -eq 'recovery'
$auditEnabled = -not [string]::IsNullOrWhiteSpace($AuditRoot)
$codesList = @(
    $Codes.Split(',') |
        ForEach-Object { $_.Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)
$codesText = $codesList -join ','
$endpointText = $Endpoint.Trim()
$endpointSeparatorIndex = $endpointText.LastIndexOf(':')
if ($endpointSeparatorIndex -le 0 -or
    $endpointSeparatorIndex -ge ($endpointText.Length - 1)) {
    throw 'endpoint_invalid'
}
$endpointHost = $endpointText.Substring(0, $endpointSeparatorIndex)
$endpointPortText = $endpointText.Substring($endpointSeparatorIndex + 1)
$endpointPort = 0
if (-not [int]::TryParse($endpointPortText, [ref]$endpointPort) -or
    $endpointPort -le 0) {
    throw 'endpoint_invalid'
}
$effectiveEndpointId = if ([string]::IsNullOrWhiteSpace($EndpointId)) {
    "mootdx_guard@$Endpoint"
}
else {
    $EndpointId
}

$cacheDirectory = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $MainRoot 'overnight_quant\data\cache'
}
else {
    $OutputDirectory
}
$standardResultPath = Join-Path `
    $cacheDirectory `
    "minute_probe_go_nogo_${Date}.json"
$resultPath = if ($isRecovery) {
    Join-Path `
        $cacheDirectory `
        "minute_probe_go_nogo_recovery_${Date}.json"
}
else {
    $standardResultPath
}

function Get-TargetTaskNames {
    $names = @($MainTaskName)
    if ($auditEnabled) {
        $names += $AuditTaskName
    }
    return @(
        $names |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Sort-Object -Unique
    )
}

function Disable-TargetTasks {
    foreach ($taskName in @(Get-TargetTaskNames)) {
        $task = Get-ScheduledTask `
            -TaskName $taskName `
            -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
        }
    }
}

function Get-Head {
    param([string]$Root)
    return ((& git -C $Root rev-parse HEAD 2>$null) | Out-String).Trim()
}

function Get-Sha256 {
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

function Test-CleanWorktree {
    param([string]$Root)
    $status = ((& git -C $Root status --porcelain 2>$null) | Out-String).Trim()
    return [string]::IsNullOrWhiteSpace($status)
}

function Get-TaskArguments {
    param($Task)
    if ($null -eq $Task) {
        return ''
    }
    return (@(
        $Task.Actions |
            ForEach-Object { [string]$_.Arguments }
    ) -join ' ')
}

function Write-ImmutableJson {
    param(
        [string]$Path,
        $Payload
    )
    if (Test-Path -LiteralPath $Path) {
        throw "go_nogo_result_already_exists:$Path"
    }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) |
        Out-Null
    $json = ($Payload | ConvertTo-Json -Depth 12) + "`n"
    $temporary = "$Path.$PID.tmp"
    [IO.File]::WriteAllText(
        $temporary,
        $json,
        [Text.UTF8Encoding]::new($false)
    )
    try {
        [IO.File]::Move($temporary, $Path)
    }
    finally {
        Remove-Item `
            -LiteralPath $temporary `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

function Get-RuntimeOutputPaths {
    $paths = @(
        (Join-Path $MainRoot "overnight_quant\data\cache\minute_label_probe_mootdx_${Date}.json"),
        (Join-Path $MainRoot "overnight_quant\data\cache\minute_label_probe_eastmoney_${Date}.json"),
        (Join-Path $MainRoot "overnight_quant\data\cache\minute_probe_watchdog_${Date}.json")
    )
    if ($auditEnabled) {
        $compactTimes = @('144957', '145001', '145008')
        foreach ($clock in $compactTimes) {
            $paths += Join-Path `
                $AuditRoot `
                "overnight_quant\data\cache\mootdx_boundary_audit_${AuditCode}_${Date}_${clock}.json"
        }
    }
    return $paths
}

function Test-TaskSettings {
    param(
        $Task,
        [string]$ExpectedRoot,
        [scriptblock]$AddCheck,
        [string]$Prefix
    )
    if ($null -eq $Task) {
        return
    }
    & $AddCheck "${Prefix}_task_ignore_new" `
        ([string]$Task.Settings.MultipleInstances -eq 'IgnoreNew') `
        ([string]$Task.Settings.MultipleInstances)
    & $AddCheck "${Prefix}_task_no_retry" `
        ([int]$Task.Settings.RestartCount -eq 0) `
        ([string]$Task.Settings.RestartCount)
    & $AddCheck "${Prefix}_task_fixed_root" `
        ((Get-TaskArguments $Task) -like "*$ExpectedRoot*") `
        (Get-TaskArguments $Task)
}

function Invoke-Validation {
    $errors = [Collections.Generic.List[string]]::new()
    $mainActualHead = Get-Head $MainRoot
    if ($mainActualHead -ne $MainHead) {
        $errors.Add("main_head:$mainActualHead")
    }
    if (-not (Test-CleanWorktree $MainRoot)) {
        $errors.Add('main_worktree_dirty')
    }
    if ($auditEnabled) {
        if ([string]::IsNullOrWhiteSpace($AuditHead) -or
            [string]::IsNullOrWhiteSpace($AuditTaskName)) {
            $errors.Add('audit_contract_incomplete')
        }
        $auditActualHead = Get-Head $AuditRoot
        if ($auditActualHead -ne $AuditHead) {
            $errors.Add("audit_head:$auditActualHead")
        }
        if (-not (Test-CleanWorktree $AuditRoot)) {
            $errors.Add('audit_worktree_dirty')
        }
    }
    foreach ($taskName in @(Get-TargetTaskNames)) {
        $task = Get-ScheduledTask `
            -TaskName $taskName `
            -ErrorAction SilentlyContinue
        if ($null -eq $task -or [string]$task.State -ne 'Disabled') {
            $errors.Add("sampling_task_not_disabled:$taskName")
        }
        if ($null -ne $task) {
            if ([string]$task.Settings.MultipleInstances -ne 'IgnoreNew') {
                $errors.Add("multiple_instances:$taskName")
            }
            if ([int]$task.Settings.RestartCount -ne 0) {
                $errors.Add("restart_count:$taskName")
            }
            if (-not [bool]$task.Settings.WakeToRun) {
                $errors.Add("wake_disabled:$taskName")
            }
            if ([bool]$task.Settings.StartWhenAvailable) {
                $errors.Add("late_start_enabled:$taskName")
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($GuardTaskName)) {
        $guard = Get-ScheduledTask `
            -TaskName $GuardTaskName `
            -ErrorAction SilentlyContinue
        if ($null -eq $guard -or [string]$guard.State -ne 'Ready') {
            $errors.Add('guard_task_not_ready')
        }
    }
    return [ordered]@{
        status = if ($errors.Count -eq 0) {
            'GO_NOGO_GUARD_VALIDATED'
        }
        else {
            'GO_NOGO_GUARD_INVALID'
        }
        execution_ok = $true
        data_ready = $false
        trade_date = $Date
        main_head = $mainActualHead
        audit_head = if ($auditEnabled) { Get-Head $AuditRoot } else { '' }
        endpoint_id = $effectiveEndpointId
        request_deadline_ms = $RequestDeadlineMs
        recovery_mode = $RecoveryMode
        errors = @($errors)
        candidates = @()
        tickets = @()
        orders = @()
    }
}

function Invoke-GoNoGo {
    $script:checks = [Collections.Generic.List[object]]::new()
    $script:go = $true
    $endpointResult = [ordered]@{
        status = 'ENDPOINT_PREFLIGHT_NOT_RUN'
        data_ready = $false
        candidates = @()
        tickets = @()
        orders = @()
    }
    $originalGuardSha256 = ''

    $addCheck = {
        param(
            [string]$Name,
            [bool]$Passed,
            [string]$Detail = ''
        )
        $script:checks.Add([ordered]@{
            name = $Name
            passed = $Passed
            detail = $Detail
        })
        if (-not $Passed) {
            $script:go = $false
        }
    }

    Disable-TargetTasks
    & $addCheck 'request_deadline_contract' `
        ($RequestDeadlineMs -eq 2000) `
        ([string]$RequestDeadlineMs)
    & $addCheck 'codes_present' ($codesList.Count -gt 0) $codesText

    $now = [datetime]::Now
    $deadlineClock = if ($isRecovery) {
        $RecoveryDeadlineClock
    }
    else {
        $DeadlineClock
    }
    $deadline = [datetime]::ParseExact(
        "$Date $deadlineClock",
        'yyyy-MM-dd HH:mm:ss',
        [Globalization.CultureInfo]::InvariantCulture
    )
    & $addCheck 'trade_date_and_go_nogo_deadline' `
        ($now.Date -eq $deadline.Date -and $now -le $deadline) `
        $now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    & $addCheck 'china_standard_time' `
        ((Get-TimeZone).Id -eq 'China Standard Time') `
        (Get-TimeZone).Id

    & w32tm /query /status *> $null
    & $addCheck 'windows_time_service' `
        ($LASTEXITCODE -eq 0) `
        "exit=$LASTEXITCODE"

    if ($isRecovery) {
        $originalValid = $false
        $originalReadError = ''
        if (Test-Path -LiteralPath $standardResultPath -PathType Leaf) {
            try {
                $originalBytes = [IO.File]::ReadAllBytes($standardResultPath)
                $originalText = [Text.UTF8Encoding]::new(
                    $false,
                    $true
                ).GetString($originalBytes)
                $original = $originalText | ConvertFrom-Json
                $originalGuardSha256 = Get-Sha256 $standardResultPath
                $originalValid = $original.status -eq 'SAMPLING_NO_GO'
            }
            catch {
                $originalValid = $false
                $originalReadError = $_.Exception.Message
            }
        }
        & $addCheck 'recovery_preserves_original_no_go' `
            $originalValid `
            $(if ($originalGuardSha256) {
                $originalGuardSha256
            }
            else {
                $originalReadError
            })
    }

    $mainActualHead = Get-Head $MainRoot
    & $addCheck 'main_fixed_head' `
        ($mainActualHead -eq $MainHead) `
        $mainActualHead
    & $addCheck 'main_worktree_clean' `
        (Test-CleanWorktree $MainRoot) `
        $MainRoot

    $auditActualHead = ''
    if ($auditEnabled) {
        $auditActualHead = Get-Head $AuditRoot
        & $addCheck 'audit_contract_complete' `
            (-not [string]::IsNullOrWhiteSpace($AuditHead) -and
                -not [string]::IsNullOrWhiteSpace($AuditTaskName)) `
            $AuditTaskName
        & $addCheck 'audit_fixed_head' `
            ($auditActualHead -eq $AuditHead) `
            $auditActualHead
        & $addCheck 'audit_worktree_clean' `
            (Test-CleanWorktree $AuditRoot) `
            $AuditRoot
    }

    $existingOutputs = @(
        Get-RuntimeOutputPaths |
            Where-Object { Test-Path -LiteralPath $_ }
    )
    & $addCheck 'runtime_outputs_absent' `
        ($existingOutputs.Count -eq 0) `
        ($existingOutputs -join ';')

    $residual = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -match (
                    'run_minute_label_probe|run_mootdx_boundary_audit|' +
                    'minute_probe_worker|run_real_collector_stress'
                )
            }
    )
    & $addCheck 'no_probe_or_stress_worker' `
        ($residual.Count -eq 0) `
        (($residual |
            ForEach-Object { $_.ProcessId } |
            Sort-Object) -join ',')

    $mainTask = Get-ScheduledTask `
        -TaskName $MainTaskName `
        -ErrorAction SilentlyContinue
    & $addCheck 'main_task_present_disabled' `
        ($null -ne $mainTask -and [string]$mainTask.State -eq 'Disabled') `
        $(if ($null -eq $mainTask) { 'missing' } else { [string]$mainTask.State })
    Test-TaskSettings $mainTask $MainRoot $addCheck 'main'

    $auditTask = $null
    if ($auditEnabled) {
        $auditTask = Get-ScheduledTask `
            -TaskName $AuditTaskName `
            -ErrorAction SilentlyContinue
        & $addCheck 'audit_task_present_disabled' `
            ($null -ne $auditTask -and [string]$auditTask.State -eq 'Disabled') `
            $(if ($null -eq $auditTask) { 'missing' } else { [string]$auditTask.State })
        Test-TaskSettings $auditTask $AuditRoot $addCheck 'audit'
    }

    $mainPython = Join-Path $MainRoot '.venv\Scripts\python.exe'
    $watchdogScript = Join-Path `
        $MainRoot `
        'overnight_quant\scripts\run_minute_probe_watchdog.ps1'
    if (Test-Path -LiteralPath $watchdogScript -PathType Leaf) {
        try {
            $watchdogRaw = & powershell.exe `
                -NoLogo `
                -NoProfile `
                -NonInteractive `
                -ExecutionPolicy Bypass `
                -File $watchdogScript `
                -Date $Date `
                -ProjectRoot $MainRoot `
                -Codes $codesText `
                -ValidateOnly
            $watchdogExit = $LASTEXITCODE
            $watchdog = ($watchdogRaw | Out-String) | ConvertFrom-Json
            & $addCheck 'main_validate_only' `
                ($watchdogExit -eq 0 -and
                    $watchdog.status -eq 'WATCHDOG_VALIDATED') `
                ([string]$watchdog.status)
        }
        catch {
            & $addCheck 'main_validate_only' $false $_.Exception.Message
        }
    }
    else {
        & $addCheck 'main_validate_only' $false 'watchdog_script_missing'
    }

    if ($auditEnabled) {
        $auditPython = Join-Path $AuditRoot '.venv\Scripts\python.exe'
        $auditScript = Join-Path `
            $AuditRoot `
            'overnight_quant\scripts\run_mootdx_boundary_audit.py'
        try {
            $auditRaw = & $auditPython `
                $auditScript `
                --date $Date `
                --endpoint $Endpoint `
                --validate-only
            $auditExit = $LASTEXITCODE
            $auditValidation = ($auditRaw | Out-String) | ConvertFrom-Json
            & $addCheck 'audit_validate_only' `
                ($auditExit -eq 0 -and
                    $auditValidation.status -eq 'BOUNDARY_AUDIT_VALIDATED') `
                ([string]$auditValidation.status)
        }
        catch {
            & $addCheck 'audit_validate_only' $false $_.Exception.Message
        }
    }

    if ($script:go) {
        $endpointScript = Join-Path `
            $MainRoot `
            'overnight_quant\scripts\run_probe_endpoint_preflight.py'
        $previousPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($previousPythonPath)) {
                $MainRoot
            }
            else {
                "$MainRoot;$previousPythonPath"
            }
            $endpointRaw = & $mainPython `
                $endpointScript `
                --endpoint $Endpoint `
                --endpoint-id $effectiveEndpointId `
                --codes $codesText `
                --deadline-ms $RequestDeadlineMs
            $endpointExit = $LASTEXITCODE
            $endpointResult = ($endpointRaw | Out-String) | ConvertFrom-Json
            & $addCheck 'mootdx_endpoint_preflight' `
                ($endpointExit -eq 0 -and
                    $endpointResult.status -eq 'ENDPOINT_PREFLIGHT_READY') `
                ($endpointResult | ConvertTo-Json -Compress)
        }
        catch {
            & $addCheck 'mootdx_endpoint_preflight' `
                $false `
                $_.Exception.Message
        }
        finally {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
    else {
        $endpointResult = [ordered]@{
            status = 'ENDPOINT_PREFLIGHT_SKIPPED_GATE_FAILURE'
            data_ready = $false
            candidates = @()
            tickets = @()
            orders = @()
        }
    }

    if ($script:go) {
        foreach ($taskName in @(Get-TargetTaskNames)) {
            Enable-ScheduledTask -TaskName $taskName | Out-Null
        }
    }

    $expectedState = if ($script:go) { 'Ready' } else { 'Disabled' }
    $finalStates = [ordered]@{}
    $stateMatches = $true
    foreach ($taskName in @(Get-TargetTaskNames)) {
        $task = Get-ScheduledTask `
            -TaskName $taskName `
            -ErrorAction SilentlyContinue
        $state = if ($null -eq $task) { 'MISSING' } else { [string]$task.State }
        $finalStates[$taskName] = $state
        if ($state -ne $expectedState) {
            $stateMatches = $false
        }
    }
    & $addCheck 'final_task_state' `
        $stateMatches `
        (($finalStates.Keys | ForEach-Object {
            "$_=$($finalStates[$_])"
        }) -join ';')
    if (-not $stateMatches -or -not $script:go) {
        Disable-TargetTasks
        $script:go = $false
        foreach ($taskName in @($finalStates.Keys)) {
            $task = Get-ScheduledTask `
                -TaskName $taskName `
                -ErrorAction SilentlyContinue
            $finalStates[$taskName] = if ($null -eq $task) {
                'MISSING'
            }
            else {
                [string]$task.State
            }
        }
    }

    $enabledTasks = @()
    if ($script:go) {
        $enabledTasks = @(Get-TargetTaskNames)
    }
    return [ordered]@{
        status = if ($script:go) { 'SAMPLING_GO' } else { 'SAMPLING_NO_GO' }
        execution_ok = $true
        data_ready = $false
        trade_date = $Date
        evaluated_at = [datetime]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        main_head = $mainActualHead
        audit_head = $auditActualHead
        endpoint_id = $effectiveEndpointId
        request_deadline_ms = $RequestDeadlineMs
        recovery_mode = $RecoveryMode
        original_guard_sha256 = $originalGuardSha256
        endpoint_preflight = $endpointResult
        checks = @($script:checks)
        enabled_tasks = $enabledTasks
        final_task_states = $finalStates
        candidates = @()
        tickets = @()
        orders = @()
    }
}

if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
    Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8
    exit 3
}

if ($ValidateOnly) {
    $validation = Invoke-Validation
    $validation | ConvertTo-Json -Depth 8
    exit $(if ($validation.status -eq 'GO_NOGO_GUARD_VALIDATED') { 0 } else { 2 })
}

try {
    $result = Invoke-GoNoGo
}
catch {
    Disable-TargetTasks
    $result = [ordered]@{
        status = 'SAMPLING_NO_GO'
        execution_ok = $false
        data_ready = $false
        trade_date = $Date
        evaluated_at = [datetime]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        endpoint_id = $effectiveEndpointId
        request_deadline_ms = $RequestDeadlineMs
        recovery_mode = $RecoveryMode
        errors = @("go_nogo_unhandled:$($_.Exception.Message)")
        enabled_tasks = @()
        candidates = @()
        tickets = @()
        orders = @()
    }
}

Write-ImmutableJson $resultPath $result
$result | ConvertTo-Json -Depth 12
exit $(if ($result.status -eq 'SAMPLING_GO') { 0 } else { 2 })
