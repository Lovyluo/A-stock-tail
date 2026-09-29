$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -AssemblyName System.Net.Http

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)] [byte[]] $Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return (($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $sha.Dispose()
    }
}

function ConvertTo-QueryString {
    param([Parameter(Mandatory = $true)] [object[]] $Pairs)
    $values = foreach ($pair in $Pairs) {
        $name = [System.Uri]::EscapeDataString([string]$pair[0])
        $value = [System.Uri]::EscapeDataString([string]$pair[1])
        "$name=$value"
    }
    return ($values -join "&")
}

function Get-InnermostExceptionType {
    param([Parameter(Mandatory = $true)] [System.Exception] $Exception)
    $current = $Exception
    while ($null -ne $current.InnerException) {
        $current = $current.InnerException
    }
    return $current.GetType().Name
}

function Write-Result {
    param([Parameter(Mandatory = $true)] [System.Collections.IDictionary] $Value)
    $Value | ConvertTo-Json -Depth 12 -Compress
}

$handler = $null
$client = $null
$request = $null
$response = $null
$cancellation = $null
$stream = $null
$bufferStream = $null
$started = $null
try {
    $inputText = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($inputText)) {
        throw [System.ArgumentException]::new("worker_input_required")
    }
    $payload = $inputText | ConvertFrom-Json
    if ([string]$payload.transport_version -ne "eastmoney_dotnet_httpclient_v1") {
        throw [System.ArgumentException]::new("transport_version_invalid")
    }
    if ([string]$payload.method -ne "GET") {
        throw [System.ArgumentException]::new("method_invalid")
    }
    $deadlineMs = [int]$payload.request_deadline_ms
    $maxBytes = [int]$payload.max_response_bytes
    if ($deadlineMs -le 0 -or $deadlineMs -gt 2000) {
        throw [System.ArgumentException]::new("deadline_invalid")
    }
    if ($maxBytes -le 0 -or $maxBytes -gt 5000000) {
        throw [System.ArgumentException]::new("response_limit_invalid")
    }
    $allowedHosts = @($payload.allowed_hosts | ForEach-Object { [string]$_ })
    $uri = [System.Uri]::new([string]$payload.url)
    if ($uri.Scheme -ne "https" -or $allowedHosts -notcontains $uri.Host -or -not $uri.IsDefaultPort) {
        throw [System.ArgumentException]::new("request_url_invalid")
    }
    $query = ConvertTo-QueryString -Pairs @($payload.params)
    $requestUrl = [string]$payload.url
    if (-not [string]::IsNullOrEmpty($query)) {
        $requestUrl = "$requestUrl`?$query"
    }

    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $false
    $handler.UseProxy = $true
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
    $request = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::Get,
        $requestUrl
    )
    foreach ($property in $payload.headers.PSObject.Properties) {
        [void]$request.Headers.TryAddWithoutValidation(
            [string]$property.Name,
            [string]$property.Value
        )
    }
    $cancellation = [System.Threading.CancellationTokenSource]::new($deadlineMs)
    $started = [System.DateTimeOffset]::Now
    $response = $client.SendAsync(
        $request,
        [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead,
        $cancellation.Token
    ).GetAwaiter().GetResult()
    $finalUri = $response.RequestMessage.RequestUri
    if ($finalUri.Scheme -ne "https" -or $allowedHosts -notcontains $finalUri.Host -or -not $finalUri.IsDefaultPort) {
        throw [System.InvalidOperationException]::new("response_url_invalid")
    }
    if ([int]$response.StatusCode -ne 200) {
        throw [System.Net.Http.HttpRequestException]::new("http_status_$([int]$response.StatusCode)")
    }
    if ($response.Content.Headers.ContentLength -gt $maxBytes) {
        throw [System.InvalidOperationException]::new("response_too_large")
    }
    $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $bufferStream = [System.IO.MemoryStream]::new()
    $buffer = [byte[]]::new(65536)
    while ($true) {
        $read = $stream.ReadAsync(
            $buffer,
            0,
            $buffer.Length,
            $cancellation.Token
        ).GetAwaiter().GetResult()
        if ($read -eq 0) { break }
        if ($bufferStream.Length + $read -gt $maxBytes) {
            throw [System.InvalidOperationException]::new("response_too_large")
        }
        $bufferStream.Write($buffer, 0, $read)
    }
    $raw = $bufferStream.ToArray()
    if ($raw.Length -eq 0) {
        throw [System.InvalidOperationException]::new("empty_response")
    }
    $completed = [System.DateTimeOffset]::Now
    $headers = [ordered]@{}
    foreach ($header in $response.Headers) {
        $headers[[string]$header.Key] = [string]($header.Value -join ",")
    }
    foreach ($header in $response.Content.Headers) {
        $headers[[string]$header.Key] = [string]($header.Value -join ",")
    }
    Write-Result -Value ([ordered]@{
        ok = $true
        transport_version = "eastmoney_dotnet_httpclient_v1"
        worker_terminated = $false
        response = [ordered]@{
            status_code = 200
            url = [string]$finalUri.AbsoluteUri
            redirect_chain = @()
            headers = $headers
            request_started_at = $started.ToString("o")
            request_completed_at = $completed.ToString("o")
            byte_count = [int]$raw.Length
            raw_hash = Get-Sha256Hex -Bytes $raw
            content_base64 = [Convert]::ToBase64String($raw)
        }
    })
    exit 0
}
catch {
    $completed = [System.DateTimeOffset]::Now
    $exception = $_.Exception
    $message = [string]$exception.Message
    $code = switch -Regex ($message) {
        "deadline|canceled|cancelled" { "MARKET_SOURCE_TIMEOUT"; break }
        "http_status_([0-9]+)" { "MARKET_SOURCE_HTTP_$($Matches[1])"; break }
        "response_too_large" { "MARKET_SOURCE_RESPONSE_TOO_LARGE"; break }
        "empty_response" { "MARKET_SOURCE_EMPTY_RESPONSE"; break }
        "request_url_invalid" { "MARKET_SOURCE_REQUEST_URL_INVALID"; break }
        "response_url_invalid" { "MARKET_SOURCE_RESPONSE_URL_INVALID"; break }
        default { "MARKET_SOURCE_REQUEST_FAILED" }
    }
    Write-Result -Value ([ordered]@{
        ok = $false
        transport_version = "eastmoney_dotnet_httpclient_v1"
        worker_terminated = $false
        error_code = $code
        exception_type = $exception.GetType().Name
        innermost_exception_type = Get-InnermostExceptionType -Exception $exception
        error = $message.Substring(0, [math]::Min(500, $message.Length))
        request_started_at = $(if ($null -ne $started) { $started.ToString("o") } else { "" })
        request_completed_at = $completed.ToString("o")
    })
    exit 2
}
finally {
    if ($null -ne $bufferStream) { $bufferStream.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    if ($null -ne $response) { $response.Dispose() }
    if ($null -ne $request) { $request.Dispose() }
    if ($null -ne $cancellation) { $cancellation.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $handler) { $handler.Dispose() }
}
