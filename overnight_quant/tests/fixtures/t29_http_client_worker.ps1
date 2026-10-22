# Explicit in-memory HttpMessageHandler. No sockets or production HTTP handler.
param([string] $HandlerAssembly = "", [switch] $CompileHandlerOnly)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$fixtureEntry = [double][System.Diagnostics.Stopwatch]::GetTimestamp() / [System.Diagnostics.Stopwatch]::Frequency
$handlerSource = Join-Path $PSScriptRoot "t29_http_handler.cs"
if ($CompileHandlerOnly) {
    if (-not [System.IO.Path]::IsPathRooted($HandlerAssembly) -or (Test-Path -LiteralPath $HandlerAssembly)) {
        throw "TEST_HANDLER_PREPARATION_TARGET_INVALID"
    }
    Add-Type -AssemblyName System.Net.Http
    Add-Type -TypeDefinition ([System.IO.File]::ReadAllText($handlerSource)) -OutputAssembly $HandlerAssembly
    @{ test_only=$true; stage="fixture_preparation_only";
       source_sha256=(Get-FileHash -LiteralPath $handlerSource -Algorithm SHA256).Hash.ToLowerInvariant();
       elapsed_ms=(([double][System.Diagnostics.Stopwatch]::GetTimestamp() / [System.Diagnostics.Stopwatch]::Frequency) - $fixtureEntry) * 1000 } |
        ConvertTo-Json -Compress
    exit 0
}
$inputValue = [Console]::In.ReadToEnd() | ConvertFrom-Json
if ($inputValue.test_only -isnot [bool] -or -not $inputValue.test_only) { throw "TEST_ONLY_REQUIRED" }
$scenario = $inputValue.test_response
$state = @{}
foreach ($p in $scenario.PSObject.Properties) { $state[$p.Name] = $p.Value }
$fixturePhases = [System.Collections.Generic.List[object]]::new()
function Set-FixturePhase([string] $Phase) {
    if ($state.ContainsKey("diagnostic_path")) {
        $seconds = [double][System.Diagnostics.Stopwatch]::GetTimestamp() / [System.Diagnostics.Stopwatch]::Frequency
        # Test-temp checkpoint survives parent termination; only failures print it.
        $parentDeadline = $null
        if ($inputValue.PSObject.Properties.Name -contains "deadline_monotonic_seconds") {
            $parentDeadline = [double]$inputValue.deadline_monotonic_seconds
        }
        $fixturePhases.Add(@{ stage=$Phase; monotonic_seconds=$seconds;
            deadline_remaining_ms=$(if($null -ne $parentDeadline){($parentDeadline - $seconds) * 1000}else{$null}) })
        @{ test_only=$true; process_id=$PID; fixture_entry_seconds=$fixtureEntry; parent_deadline_monotonic_seconds=$parentDeadline;
           phases=@($fixturePhases.ToArray()) } |
            ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath $state.diagnostic_path -Encoding UTF8
    }
}
Set-FixturePhase "runtime_initialization"
Add-Type -AssemblyName System.Net.Http
. "$PSScriptRoot/../../scripts/market_http_start_gate.ps1"
if ($HandlerAssembly) {
    Set-FixturePhase "fixture_assembly_load"
    Add-Type -Path $HandlerAssembly
} else {
    Set-FixturePhase "fixture_compile"
    Add-Type -TypeDefinition ([System.IO.File]::ReadAllText($handlerSource))
}
Set-FixturePhase "fixture_initialized"
$client = $null
$request = $null
$cancellation = $null
try {
    if ($state.ContainsKey("ready_marker")) { [System.IO.File]::WriteAllText($state.ready_marker, "ready") }
    if ($state.ContainsKey("release_marker")) {
        while (-not [System.IO.File]::Exists($state.release_marker)) { [System.Threading.Thread]::Sleep(1) }
    }
    $deadline = (Get-ClientMonotonicSeconds) + 2.0
    if ($inputValue.PSObject.Properties.Name -contains "deadline_monotonic_seconds") { $deadline = [double]$inputValue.deadline_monotonic_seconds }
    if ($state.ContainsKey("budget_ms")) { $deadline = (Get-ClientMonotonicSeconds) + [double]$state.budget_ms / 1000 }
    if ($state.ContainsKey("cold_ms")) {
        Set-FixturePhase "fixture_cold_delay"
        [System.Threading.Thread]::Sleep([int]$state.cold_ms)
    }
    $handler = [T29Handler]::new()
    $handler.Marker = [string]$state.call_marker
    if ($state.ContainsKey("body")) { $handler.Body = $state.body | ConvertTo-Json -Depth 16 -Compress }
    if ($state.ContainsKey("block_ms")) { $handler.BlockMs = [int]$state.block_ms }
    if ($state.ContainsKey("body_ms")) { $handler.BodyMs = [int]$state.body_ms }
    $client = [System.Net.Http.HttpClient]::new($handler)
    $requestUrl = [string]$inputValue.url
    if ($inputValue.PSObject.Properties.Name -contains "params") {
        $query = @($inputValue.params | ForEach-Object { [Uri]::EscapeDataString([string]$_[0]) + "=" + [Uri]::EscapeDataString([string]$_[1]) }) -join "&"
        if ($query) { $requestUrl += "?" + $query }
    }
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $requestUrl)
    $remaining = [math]::Floor(($deadline - (Get-ClientMonotonicSeconds)) * 1000)
    if ($remaining -le 0) { throw "http_start_budget_exhausted" }
    $cancellation = [System.Threading.CancellationTokenSource]::new([int]$remaining)
    if ($state.ContainsKey("wait_for_deadline_timer") -and $state.wait_for_deadline_timer) {
        [void]$cancellation.Token.WaitHandle.WaitOne()
    }
    Set-FixturePhase "coordination_wait"
    $call = Invoke-RateLimitedHttpSend $client $request $cancellation.Token $deadline $inputValue.http_start_state_directory
    Set-FixturePhase "response_read"
    $response = $call.Task.GetAwaiter().GetResult()
    $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $rawStream = [System.IO.MemoryStream]::new()
    $buffer = [byte[]]::new(4096)
    while ($true) {
        $n = $stream.ReadAsync($buffer, 0, $buffer.Length, $cancellation.Token).GetAwaiter().GetResult()
        if ($n -eq 0) { break }
        $rawStream.Write($buffer, 0, $n)
    }
    $wallClock = { [System.DateTimeOffset]::Now.ToOffset([System.TimeSpan]::FromHours(8)) }
    if ($state.ContainsKey("clock_delta_ms")) {
        $wallClock = { [System.DateTimeOffset]::Now.ToOffset([System.TimeSpan]::FromHours(8)).AddMilliseconds([double]$state.clock_delta_ms) }
    }
    $timing = Get-HttpReadCompletion $call.StartedAt $call.StartedSeconds $deadline $wallClock
    $raw = $rawStream.ToArray()
    Set-FixturePhase "response_read_complete"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = (($sha.ComputeHash($raw) | ForEach-Object { $_.ToString("x2") }) -join "")
    # Explicit synthetic wall-clock axis for same-round CLI fixtures only.
    $captureStarted = $call.StartedAt
    $captureCompleted = $timing.CompletedAt
    if ($state.ContainsKey("capture_offset_seconds")) {
        $captureStarted = $captureStarted.AddSeconds([double]$state.capture_offset_seconds)
        $captureCompleted = $captureCompleted.AddSeconds([double]$state.capture_offset_seconds)
    }
    $result = @{ ok=$true; test_only=$true; transport_version="eastmoney_dotnet_httpclient_v1"; worker_terminated=$false;
        response=@{status_code=200; url=$requestUrl; redirect_chain=@(); byte_count=$raw.Length;
            raw_hash=$hash; content_base64=[Convert]::ToBase64String($raw);
            request_started_at=$captureStarted.ToString("o"); request_completed_at=$captureCompleted.ToString("o");
            client_read_elapsed_ms=$timing.ElapsedMs; http_start_wait_ms=$call.WaitElapsedMs;
            client_call_started_monotonic_seconds=$call.StartedSeconds; body_completed_monotonic_seconds=$timing.CompletedSeconds;
            http_start_coordination_version=$call.CoordinationVersion;
            request_started_at_semantics="client_SendAsync_call_boundary_not_packet_transmission";
            request_completed_at_semantics="response_body_fully_read"}}
    $result | ConvertTo-Json -Depth 8 -Compress
    exit 0
} catch {
    Set-FixturePhase "failed"
    $rawError = [string]$_.Exception.Message
    $inner = $_.Exception
    $cancelled = $false
    while ($null -ne $inner) {
        if ($inner -is [System.OperationCanceledException]) { $cancelled = $true; break }
        $inner = $inner.InnerException
    }
    $called = $null -ne $state -and $state.ContainsKey("call_marker") -and [System.IO.File]::Exists($state.call_marker)
    $timerCancelled = $null -ne $cancellation -and $cancellation.IsCancellationRequested
    $code = $rawError
    # This fixture's token is owned solely by its deadline timer, never a caller.
    if ($cancelled -and $timerCancelled -and -not $called) { $code = "http_start_budget_exhausted" }
    @{ok=$false; test_only=$true; transport_version="eastmoney_dotnet_httpclient_v1";
      error_code=$code; raw_error_message=$rawError; deadline_timer_cancelled=$timerCancelled;
      raw_error_type=$(if ($null -ne $inner) { $inner.GetType().FullName } else { $_.Exception.GetType().FullName });
      http_call_made=$called} | ConvertTo-Json -Compress
    exit 2
} finally {
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $request) { $request.Dispose() }
    if ($null -ne $cancellation) { $cancellation.Dispose() }
    Set-FixturePhase "disposed"
}
