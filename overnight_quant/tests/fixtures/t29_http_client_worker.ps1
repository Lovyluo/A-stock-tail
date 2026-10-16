# Explicit in-memory HttpMessageHandler. No sockets or production HTTP handler.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Net.Http
. "$PSScriptRoot/../../scripts/market_http_start_gate.ps1"
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Diagnostics;
using System.Globalization;
public class T29Handler : HttpMessageHandler {
    public string Marker;
    public string Body = "{\"test_only\":true}";
    public int BlockMs;
    public int BodyMs;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t) {
        File.WriteAllText(Marker, ((double)Stopwatch.GetTimestamp()/Stopwatch.Frequency).ToString("R", CultureInfo.InvariantCulture));
        if (BlockMs > 0) Thread.Sleep(BlockMs);
        var response = new HttpResponseMessage(HttpStatusCode.OK);
        response.RequestMessage = r;
        response.Content = new StreamContent(new T29Body(BodyMs, Body));
        return Task.FromResult(response);
    }
}
public class T29Body : MemoryStream {
    private int DelayMs;
    public T29Body(int delay, string body) : base(System.Text.Encoding.UTF8.GetBytes(body)) { DelayMs=delay; }
    public override async Task<int> ReadAsync(byte[] b, int o, int n, CancellationToken t) {
        if (DelayMs > 0) { int d=DelayMs; DelayMs=0; await Task.Delay(d,t); }
        return await base.ReadAsync(b,o,n,t);
    }
}
'@
$client = $null
$request = $null
$cancellation = $null
$state = $null
try {
    $inputValue = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($inputValue.test_only -isnot [bool] -or -not $inputValue.test_only) { throw "TEST_ONLY_REQUIRED" }
    $scenario = $inputValue.test_response
    $state = @{}
    foreach ($p in $scenario.PSObject.Properties) { $state[$p.Name] = $p.Value }
    if ($state.ContainsKey("ready_marker")) { [System.IO.File]::WriteAllText($state.ready_marker, "ready") }
    if ($state.ContainsKey("release_marker")) {
        while (-not [System.IO.File]::Exists($state.release_marker)) { [System.Threading.Thread]::Sleep(1) }
    }
    $deadline = (Get-ClientMonotonicSeconds) + 2.0
    if ($inputValue.PSObject.Properties.Name -contains "deadline_monotonic_seconds") { $deadline = [double]$inputValue.deadline_monotonic_seconds }
    if ($state.ContainsKey("budget_ms")) { $deadline = (Get-ClientMonotonicSeconds) + [double]$state.budget_ms / 1000 }
    if ($state.ContainsKey("cold_ms")) { [System.Threading.Thread]::Sleep([int]$state.cold_ms) }
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
    $call = Invoke-RateLimitedHttpSend $client $request $cancellation.Token $deadline $inputValue.http_start_state_directory
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
}
