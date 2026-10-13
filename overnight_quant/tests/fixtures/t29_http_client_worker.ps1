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
    public int BlockMs;
    public int BodyMs;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t) {
        File.WriteAllText(Marker, ((double)Stopwatch.GetTimestamp()/Stopwatch.Frequency).ToString("R", CultureInfo.InvariantCulture));
        if (BlockMs > 0) Thread.Sleep(BlockMs);
        var response = new HttpResponseMessage(HttpStatusCode.OK);
        response.RequestMessage = r;
        response.Content = new StreamContent(new T29Body(BodyMs));
        return Task.FromResult(response);
    }
}
public class T29Body : MemoryStream {
    private int DelayMs;
    public T29Body(int delay) : base(System.Text.Encoding.UTF8.GetBytes("{\"test_only\":true}")) { DelayMs=delay; }
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
    if ($state.ContainsKey("block_ms")) { $handler.BlockMs = [int]$state.block_ms }
    if ($state.ContainsKey("body_ms")) { $handler.BodyMs = [int]$state.body_ms }
    $client = [System.Net.Http.HttpClient]::new($handler)
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, [string]$inputValue.url)
    $remaining = [math]::Floor(($deadline - (Get-ClientMonotonicSeconds)) * 1000)
    if ($remaining -le 0) { throw "http_start_budget_exhausted" }
    $cancellation = [System.Threading.CancellationTokenSource]::new([int]$remaining)
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
    $result = @{ ok=$true; test_only=$true; transport_version="eastmoney_dotnet_httpclient_v1"; worker_terminated=$false;
        response=@{status_code=200; url=[string]$inputValue.url; redirect_chain=@(); byte_count=$raw.Length;
            raw_hash=$hash; content_base64=[Convert]::ToBase64String($raw);
            request_started_at=$call.StartedAt.ToString("o"); request_completed_at=$timing.CompletedAt.ToString("o");
            client_read_elapsed_ms=$timing.ElapsedMs; http_start_wait_ms=$call.WaitElapsedMs;
            client_call_started_monotonic_seconds=$call.StartedSeconds; body_completed_monotonic_seconds=$timing.CompletedSeconds}}
    $result | ConvertTo-Json -Depth 8 -Compress
    exit 0
} catch {
    @{ok=$false; test_only=$true; transport_version="eastmoney_dotnet_httpclient_v1";
      error_code=[string]$_.Exception.Message; http_call_made=($null -ne $state -and $state.ContainsKey("call_marker") -and [System.IO.File]::Exists($state.call_marker))} | ConvertTo-Json -Compress
    exit 2
} finally {
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $request) { $request.Dispose() }
    if ($null -ne $cancellation) { $cancellation.Dispose() }
}
