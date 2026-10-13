# Shared state protects the HttpClient call boundary, not network packet timing.
function Get-ClientMonotonicSeconds {
    return [double][System.Diagnostics.Stopwatch]::GetTimestamp() / [System.Diagnostics.Stopwatch]::Frequency
}

function Set-HttpStartState {
    param($Stream, $Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Compress) + "`n")
    # Never erase the pending marker if killed while committing a completed call.
    $Stream.Position = $Stream.Length
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush($true)
}

function Invoke-RateLimitedHttpSend {
    param(
        [System.Net.Http.HttpClient] $Client,
        [System.Net.Http.HttpRequestMessage] $Request,
        [System.Threading.CancellationToken] $Token,
        [double] $DeadlineSeconds,
        [string] $StateDirectory = ([System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "a-stock-http-start-v1"))
    )
    if (-not [System.IO.Path]::IsPathRooted($StateDirectory)) {
        throw [System.ArgumentException]::new("http_start_coordination_invalid")
    }
    [void][System.IO.Directory]::CreateDirectory($StateDirectory)
    $hostName = $Request.RequestUri.Host.ToLowerInvariant()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $key = (($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($hostName)) | ForEach-Object { $_.ToString("x2") }) -join "")
    } finally { $sha.Dispose() }
    $path = [System.IO.Path]::Combine($StateDirectory, "$key.json")
    $stream = $null
    $waitingSince = Get-ClientMonotonicSeconds
    try {
        while ($null -eq $stream) {
            $Token.ThrowIfCancellationRequested()
            if ((Get-ClientMonotonicSeconds) -ge $DeadlineSeconds) {
                throw [System.TimeoutException]::new("http_start_budget_exhausted")
            }
            try {
                $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::OpenOrCreate,
                    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            } catch [System.IO.IOException] {
                # Only sharing/lock violations represent another live coordinator.
                if (($_.Exception.HResult -band 0xffff) -notin @(32, 33)) { throw }
                [System.Threading.Thread]::Sleep(1)
            }
        }
        $last = 0.0
        if ($stream.Length -gt 0) {
            if ($stream.Length -gt 5000000) { throw "http_start_coordination_journal_full" }
            $bytes = [byte[]]::new([int]$stream.Length)
            if ($stream.Read($bytes, 0, $bytes.Length) -ne $bytes.Length) { throw "http_start_coordination_invalid" }
            $journal = [System.Text.Encoding]::UTF8.GetString($bytes)
            if (-not $journal.EndsWith("`n")) { throw "http_start_coordination_uncertain" }
            $state = ($journal.TrimEnd("`n").Split("`n") | Select-Object -Last 1) | ConvertFrom-Json
            if ($state.version -ne "http_client_call_spacing_v1" -or $state.host_name -ne $hostName -or
                $state.pending -isnot [bool] -or $state.pending) { throw "http_start_coordination_uncertain" }
            $last = [double]$state.last_call_end_seconds
            if ([double]::IsNaN($last) -or [double]::IsInfinity($last) -or $last -lt 0 -or $last -gt (Get-ClientMonotonicSeconds)) {
                throw "http_start_coordination_invalid"
            }
        }
        while ((Get-ClientMonotonicSeconds) -lt $last + 0.5) {
            $Token.ThrowIfCancellationRequested()
            if ($last + 0.5 -ge $DeadlineSeconds) { throw "http_start_budget_exhausted" }
            [System.Threading.Thread]::Sleep(1)
        }
        $Token.ThrowIfCancellationRequested()
        if ((Get-ClientMonotonicSeconds) + 0.001 -ge $DeadlineSeconds) { throw "http_start_budget_exhausted" }
        # If killed during the call, the persisted pending marker fails closed.
        Set-HttpStartState $stream @{ version = "http_client_call_spacing_v1"; host_name = $hostName; pending = $true; last_call_end_seconds = $last }
        $Token.ThrowIfCancellationRequested()
        if ((Get-ClientMonotonicSeconds) + 0.001 -ge $DeadlineSeconds) {
            Set-HttpStartState $stream @{ version = "http_client_call_spacing_v1"; host_name = $hostName; pending = $false; last_call_end_seconds = $last }
            throw "http_start_budget_exhausted"
        }
        $startedAt = [System.DateTimeOffset]::Now.ToOffset([System.TimeSpan]::FromHours(8))
        $startedSeconds = Get-ClientMonotonicSeconds
        $task = $Client.SendAsync($Request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead, $Token)
        # A post-call conservative marker prevents compressed intervals after stalls.
        $callEnd = Get-ClientMonotonicSeconds
        Set-HttpStartState $stream @{ version = "http_client_call_spacing_v1"; host_name = $hostName; pending = $false; last_call_end_seconds = $callEnd }
        return @{
            Task = $task; StartedAt = $startedAt; StartedSeconds = $startedSeconds
            CoordinationVersion = "http_client_call_spacing_v1"
            WaitElapsedMs = ($startedSeconds - $waitingSince) * 1000
        }
    } finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Get-HttpReadCompletion {
    param([System.DateTimeOffset] $StartedAt, [double] $StartedSeconds, [double] $DeadlineSeconds,
          [scriptblock] $WallClock = { [System.DateTimeOffset]::Now.ToOffset([System.TimeSpan]::FromHours(8)) })
    $completedSeconds = Get-ClientMonotonicSeconds
    $completedAt = & $WallClock
    if ($completedSeconds -ge $DeadlineSeconds) { throw "http_body_deadline_exceeded" }
    $wallElapsed = ($completedAt - $StartedAt).TotalMilliseconds
    $monotonicElapsed = ($completedSeconds - $StartedSeconds) * 1000
    # Instrumentation guard only: never adds tolerance to source event ordering.
    if ($completedAt -lt $StartedAt -or $wallElapsed -gt 2000 -or
        [math]::Abs($wallElapsed - $monotonicElapsed) -gt 25) {
        throw "http_wall_clock_order_invalid"
    }
    return @{ CompletedAt = $completedAt; CompletedSeconds = $completedSeconds
              ElapsedMs = $monotonicElapsed }
}
