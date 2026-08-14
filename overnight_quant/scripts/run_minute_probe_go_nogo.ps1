[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
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
    [int]$RequestDeadlineMs = 2000,
    [string]$DeadlineClock = '14:30:00',
    [string]$RecoveryDeadlineClock = '14:39:30',
    [string]$RecoveryMode = 'standard',
    [string]$OutputDirectory = '',
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:formalCodes = @(
    '000001',
    '000333',
    '600000',
    '600519',
    '601318'
)
$script:formalCodesText = $script:formalCodes -join ','
$script:isRecovery = $RecoveryMode -eq 'recovery'
$script:auditEnabled = -not [string]::IsNullOrWhiteSpace($AuditRoot)
$script:disableErrors = [Collections.Generic.List[string]]::new()
$script:cacheDirectory = ''
$script:standardResultPath = ''
$script:resultPath = ''
$script:codesList = @()
$script:codesText = ''
$script:codesValid = $false
$script:endpointValid = $false
$script:endpointHost = ''
$script:endpointPort = 0
$script:effectiveEndpointId = if ([string]::IsNullOrWhiteSpace($EndpointId)) {
    "mootdx_guard@$Endpoint"
}
else {
    $EndpointId
}

function Get-TargetTaskNames {
    $names = @($MainTaskName)
    if ($script:auditEnabled) {
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
        try {
            $task = Get-ScheduledTask `
                -TaskName $taskName `
                -ErrorAction SilentlyContinue
            if ($null -ne $task) {
                Disable-ScheduledTask -TaskName $taskName | Out-Null
            }
        }
        catch {
            $script:disableErrors.Add(
                "disable_task_failed:${taskName}:$($_.Exception.Message)"
            )
        }
    }
}

function Initialize-Inputs {
    $script:cacheDirectory = if (
        [string]::IsNullOrWhiteSpace($OutputDirectory)
    ) {
        Join-Path $MainRoot 'overnight_quant\data\cache'
    }
    else {
        $OutputDirectory
    }
    $script:standardResultPath = Join-Path `
        $script:cacheDirectory `
        "minute_probe_go_nogo_${Date}.json"
    $script:resultPath = if ($script:isRecovery) {
        Join-Path `
            $script:cacheDirectory `
            "minute_probe_go_nogo_recovery_${Date}.json"
    }
    else {
        $script:standardResultPath
    }

    $codeContract = ConvertTo-CodeContract $Codes
    $script:codesList = @($codeContract.codes)
    $script:codesText = $script:codesList -join ','
    $script:codesValid = [bool]$codeContract.valid

    $endpointContract = ConvertTo-EndpointContract $Endpoint
    $script:endpointValid = [bool]$endpointContract.valid
    $script:endpointHost = [string]$endpointContract.host
    $script:endpointPort = [int]$endpointContract.port
}

function ConvertTo-CodeContract {
    param([string]$Value)
    $items = @(
        ([string]$Value).Split(',') |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $validShape = $items.Count -gt 0
    foreach ($item in $items) {
        if ($item -notmatch '^\d{6}$') {
            $validShape = $false
        }
    }
    $duplicates = @($items | Group-Object | Where-Object Count -gt 1)
    $exact = (
        $validShape -and
        $duplicates.Count -eq 0 -and
        ($items -join ',') -ceq $script:formalCodesText
    )
    return [pscustomobject]@{
        valid = $exact
        codes = $items
        normalized = $items -join ','
    }
}

function ConvertTo-EndpointContract {
    param([string]$Value)
    $text = ([string]$Value).Trim()
    $separatorIndex = $text.LastIndexOf(':')
    if ($separatorIndex -le 0 -or
        $separatorIndex -ge ($text.Length - 1)) {
        return [pscustomobject]@{ valid = $false; host = ''; port = 0 }
    }
    $endpointHostValue = $text.Substring(0, $separatorIndex).Trim()
    $portText = $text.Substring($separatorIndex + 1)
    $port = 0
    $valid = (
        -not [string]::IsNullOrWhiteSpace($endpointHostValue) -and
        [int]::TryParse($portText, [ref]$port) -and
        $port -gt 0 -and
        $port -le 65535
    )
    return [pscustomobject]@{
        valid = $valid
        host = $endpointHostValue
        port = $port
    }
}

function Get-Head {
    param([string]$Root)
    $value = ((& git -C $Root rev-parse HEAD 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "git_head_failed:$Root"
    }
    return $value
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
    if ($LASTEXITCODE -ne 0) {
        throw "git_status_failed:$Root"
    }
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

function Get-TaskCodeContract {
    param($Task)
    $arguments = Get-TaskArguments $Task
    $pattern = '(?i)(?:--codes|-Codes)\s+(?:"(?<dq>[^"]+)"|''(?<sq>[^'']+)''|(?<raw>[^\s]+))'
    $match = [regex]::Match($arguments, $pattern)
    if (-not $match.Success) {
        return [pscustomobject]@{
            valid = $false
            codes = @()
            normalized = ''
        }
    }
    $value = @(
        $match.Groups['dq'].Value,
        $match.Groups['sq'].Value,
        $match.Groups['raw'].Value
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    return ConvertTo-CodeContract ([string]$value)
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
    if ($script:auditEnabled) {
        foreach ($clock in @('144957', '145001', '145008')) {
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
        [string]$Prefix,
        [switch]$RequireFormalCodes
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
    if ($RequireFormalCodes) {
        $taskCodes = Get-TaskCodeContract $Task
        & $AddCheck "${Prefix}_task_formal_codes_exact" `
            ([bool]$taskCodes.valid) `
            ([string]$taskCodes.normalized)
    }
}

function New-FailureResult {
    param(
        [string]$ErrorText,
        [bool]$ExecutionOk = $false
    )
    $states = [ordered]@{}
    foreach ($taskName in @(Get-TargetTaskNames)) {
        try {
            $task = Get-ScheduledTask `
                -TaskName $taskName `
                -ErrorAction SilentlyContinue
            $states[$taskName] = if ($null -eq $task) {
                'MISSING'
            }
            else {
                [string]$task.State
            }
        }
        catch {
            $states[$taskName] = 'UNKNOWN'
        }
    }
    return [ordered]@{
        status = 'SAMPLING_NO_GO'
        execution_ok = $ExecutionOk
        data_ready = $false
        trade_date = $Date
        evaluated_at = [datetime]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        endpoint_id = $script:effectiveEndpointId
        request_deadline_ms = $RequestDeadlineMs
        recovery_mode = $RecoveryMode
        errors = @($ErrorText) + @($script:disableErrors)
        enabled_tasks = @()
        final_task_states = $states
        candidates = @()
        tickets = @()
        orders = @()
    }
}

function Invoke-Validation {
    $errors = [Collections.Generic.List[string]]::new()
    if ($RecoveryMode -notin @('standard', 'recovery')) {
        $errors.Add('recovery_mode_invalid')
    }
    if (-not $script:codesValid) {
        $errors.Add("formal_codes_mismatch:$($script:codesText)")
    }
    if (-not $script:endpointValid) {
        $errors.Add('endpoint_invalid')
    }
    if ($RequestDeadlineMs -ne 2000) {
        $errors.Add("request_deadline_invalid:$RequestDeadlineMs")
    }
    try {
        [void][datetime]::ParseExact(
            $Date,
            'yyyy-MM-dd',
            [Globalization.CultureInfo]::InvariantCulture
        )
    }
    catch {
        $errors.Add('trade_date_invalid')
    }

    $mainActualHead = ''
    try {
        $mainActualHead = Get-Head $MainRoot
        if ($mainActualHead -ne $MainHead) {
            $errors.Add("main_head:$mainActualHead")
        }
        if (-not (Test-CleanWorktree $MainRoot)) {
            $errors.Add('main_worktree_dirty')
        }
    }
    catch {
        $errors.Add($_.Exception.Message)
    }
    if ($script:auditEnabled) {
        try {
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
        catch {
            $errors.Add($_.Exception.Message)
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
            if ($taskName -eq $MainTaskName -and
                -not [bool](Get-TaskCodeContract $task).valid) {
                $errors.Add('main_task_formal_codes_mismatch')
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
        endpoint_id = $script:effectiveEndpointId
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

    & $addCheck 'tasks_disabled_before_validation' `
        ($script:disableErrors.Count -eq 0) `
        (@($script:disableErrors) -join ';')
    & $addCheck 'recovery_mode_contract' `
        ($RecoveryMode -in @('standard', 'recovery')) `
        $RecoveryMode
    & $addCheck 'request_deadline_contract' `
        ($RequestDeadlineMs -eq 2000) `
        ([string]$RequestDeadlineMs)
    & $addCheck 'formal_codes_exact' `
        $script:codesValid `
        $script:codesText
    & $addCheck 'endpoint_contract' `
        $script:endpointValid `
        $Endpoint

    $now = [datetime]::Now
    try {
        $deadlineClock = if ($script:isRecovery) {
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
    }
    catch {
        & $addCheck 'trade_date_and_go_nogo_deadline' `
            $false `
            $_.Exception.Message
    }
    & $addCheck 'china_standard_time' `
        ((Get-TimeZone).Id -eq 'China Standard Time') `
        (Get-TimeZone).Id

    & w32tm /query /status *> $null
    & $addCheck 'windows_time_service' `
        ($LASTEXITCODE -eq 0) `
        "exit=$LASTEXITCODE"

    if ($script:isRecovery) {
        $originalValid = $false
        $originalReadError = ''
        if (Test-Path -LiteralPath $script:standardResultPath -PathType Leaf) {
            try {
                $originalBytes = [IO.File]::ReadAllBytes(
                    $script:standardResultPath
                )
                $originalText = [Text.UTF8Encoding]::new(
                    $false,
                    $true
                ).GetString($originalBytes)
                $original = $originalText | ConvertFrom-Json
                $originalGuardSha256 = Get-Sha256 `
                    $script:standardResultPath
                $originalValid = $original.status -eq 'SAMPLING_NO_GO'
            }
            catch {
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
    if ($script:auditEnabled) {
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
    Test-TaskSettings `
        $mainTask `
        $MainRoot `
        $addCheck `
        'main' `
        -RequireFormalCodes

    $auditTask = $null
    if ($script:auditEnabled) {
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
                -Codes $script:codesText `
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

    if ($script:auditEnabled) {
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
            $env:PYTHONPATH = if (
                [string]::IsNullOrWhiteSpace($previousPythonPath)
            ) {
                $MainRoot
            }
            else {
                "$MainRoot;$previousPythonPath"
            }
            $endpointRaw = & $mainPython `
                $endpointScript `
                --endpoint $Endpoint `
                --endpoint-id $script:effectiveEndpointId `
                --codes $script:codesText `
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
        endpoint_id = $script:effectiveEndpointId
        request_deadline_ms = $RequestDeadlineMs
        recovery_mode = $RecoveryMode
        original_guard_sha256 = $originalGuardSha256
        endpoint_preflight = $endpointResult
        checks = @($script:checks)
        errors = @($script:disableErrors)
        enabled_tasks = $enabledTasks
        final_task_states = $finalStates
        candidates = @()
        tickets = @()
        orders = @()
    }
}

if ($ValidateOnly) {
    try {
        Initialize-Inputs
        $validation = Invoke-Validation
    }
    catch {
        $validation = New-FailureResult `
            "validate_only_unhandled:$($_.Exception.Message)"
    }
    $validation | ConvertTo-Json -Depth 12
    exit $(if ($validation.status -eq 'GO_NOGO_GUARD_VALIDATED') { 0 } else { 2 })
}

# Every mutating path disables targets before parsing endpoint, code, Git,
# existing-result, or output contracts.
Disable-TargetTasks
try {
    Initialize-Inputs
    if (Test-Path -LiteralPath $script:resultPath -PathType Leaf) {
        try {
            Get-Content -LiteralPath $script:resultPath -Raw -Encoding UTF8
        }
        catch {
            (New-FailureResult `
                "existing_result_unreadable:$($_.Exception.Message)") |
                ConvertTo-Json -Depth 12
        }
        exit 3
    }
    $result = Invoke-GoNoGo
}
catch {
    Disable-TargetTasks
    $result = New-FailureResult `
        "go_nogo_unhandled:$($_.Exception.Message)"
}

try {
    Write-ImmutableJson $script:resultPath $result
}
catch {
    Disable-TargetTasks
    $writeFailure = New-FailureResult `
        "go_nogo_write_failed:$($_.Exception.Message)"
    $writeFailure | ConvertTo-Json -Depth 12
    exit 4
}

$result | ConvertTo-Json -Depth 12
exit $(if ($result.status -eq 'SAMPLING_GO') { 0 } else { 2 })
