function New-WatchdogSourceState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source
    )

    return [ordered]@{
        source = $Source
        lifecycle = 'NOT_STARTED'
        adopted_running_process = $false
        scheduled_task_attempted = $false
        scheduled_start_accepted = $false
        direct_start_attempted = $false
        direct_start_accepted = $false
        terminal_output_present = $false
        error_code = ''
        data_ready = $false
        candidates = @()
        tickets = @()
        orders = @()
    }
}

function Invoke-WatchdogSourceStep {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$State,

        [Parameter(Mandatory = $true)]
        [scriptblock]$OutputExists,

        [Parameter(Mandatory = $true)]
        [scriptblock]$ProcessRunning,

        [Parameter(Mandatory = $true)]
        [scriptblock]$CanStart,

        [Parameter(Mandatory = $true)]
        [scriptblock]$ScheduledTaskExists,

        [Parameter(Mandatory = $true)]
        [scriptblock]$StartScheduledTask,

        [Parameter(Mandatory = $true)]
        [scriptblock]$ScheduledTaskRunning,

        [Parameter(Mandatory = $true)]
        [scriptblock]$AfterScheduledStart,

        [Parameter(Mandatory = $true)]
        [scriptblock]$StartDirect,

        [Parameter(Mandatory = $true)]
        [scriptblock]$RecordEvent
    )

    $source = [string]$State.source
    if (& $OutputExists $source) {
        if (-not $State.terminal_output_present) {
            $State.terminal_output_present = $true
            $State.lifecycle = 'TERMINAL_OUTPUT_PRESENT'
            & $RecordEvent `
                $source `
                'terminal_output_already_present' `
                ''
        }
        return
    }

    if ([string]$State.lifecycle -ne 'NOT_STARTED') {
        return
    }

    if (& $ProcessRunning $source) {
        $State.adopted_running_process = $true
        $State.lifecycle = 'ADOPTED_RUNNING_PROCESS'
        & $RecordEvent $source 'existing_process_adopted' ''
        return
    }

    if (-not (& $CanStart $source)) {
        $State.lifecycle = 'START_WINDOW_CLOSED'
        & $RecordEvent $source 'late_restart_blocked' 'past_safe_start'
        return
    }

    if (& $ScheduledTaskExists $source) {
        $State.scheduled_task_attempted = $true
        $State.lifecycle = 'SCHEDULED_TASK_ATTEMPTED'
        & $RecordEvent $source 'scheduled_task_attempted' ''
        try {
            & $StartScheduledTask $source
        }
        catch {
            $State.lifecycle = 'SCHEDULED_TASK_START_FAILED'
            $State.error_code = 'SCHEDULED_TASK_START_FAILED'
            & $RecordEvent `
                $source `
                'scheduled_task_start_failed' `
                $_.Exception.Message
            return
        }

        $State.scheduled_start_accepted = $true
        $State.lifecycle = 'SCHEDULED_TASK_ACCEPTED'
        & $RecordEvent $source 'scheduled_task_start_accepted' ''
        & $AfterScheduledStart $source

        if (& $OutputExists $source) {
            $State.terminal_output_present = $true
            $State.lifecycle = 'TERMINAL_OUTPUT_PRESENT'
            & $RecordEvent `
                $source `
                'terminal_output_after_scheduled_start' `
                ''
            return
        }
        if (& $ProcessRunning $source) {
            $State.lifecycle = 'SCHEDULED_PROCESS_RUNNING'
            & $RecordEvent $source 'scheduled_process_running' ''
            return
        }
        if (& $ScheduledTaskRunning $source) {
            $State.lifecycle = 'SCHEDULED_TASK_RUNNING'
            & $RecordEvent $source 'scheduled_task_running' ''
            return
        }

        $State.lifecycle = 'SCHEDULED_TASK_COMPLETED_OR_DETACHED'
        & $RecordEvent `
            $source `
            'scheduled_task_completed_or_detached' `
            ''
        return
    }

    $State.direct_start_attempted = $true
    $State.lifecycle = 'DIRECT_START_ATTEMPTED'
    & $RecordEvent $source 'scheduled_task_missing' ''
    try {
        $detail = & $StartDirect $source
    }
    catch {
        $State.lifecycle = 'DIRECT_START_FAILED'
        $State.error_code = 'DIRECT_START_FAILED'
        & $RecordEvent `
            $source `
            'direct_start_failed' `
            $_.Exception.Message
        return
    }

    $State.direct_start_accepted = $true
    $State.lifecycle = 'DIRECT_START_ACCEPTED'
    & $RecordEvent $source 'direct_fallback_started' ([string]$detail)
}
