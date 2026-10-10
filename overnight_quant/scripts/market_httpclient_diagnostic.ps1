$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -AssemblyName System.Net.Http

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

$inputText = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputText)) {
    throw "diagnostic_input_required"
}
$payload = $inputText | ConvertFrom-Json
$deadlineMs = [int]$payload.request_deadline_ms
$maxResponseBytes = [int]$payload.max_response_bytes

$probeUri = [System.Uri]::new([string]$payload.requests[0].url)
$systemProxy = [System.Net.WebRequest]::GetSystemWebProxy()
$proxyUri = $systemProxy.GetProxy($probeUri)
$proxyBypassed = $systemProxy.IsBypassed($probeUri)
$proxyEndpoint = ""
if (-not $proxyBypassed -and $null -ne $proxyUri) {
    $proxyEndpoint = "$($proxyUri.Scheme)://$($proxyUri.Host)"
    if (-not $proxyUri.IsDefaultPort) {
        $proxyEndpoint += ":$($proxyUri.Port)"
    }
}

$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$handler.UseCookies = $false
$handler.UseProxy = $true
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan

$results = [System.Collections.Generic.List[object]]::new()
try {
    foreach ($requestSpec in @($payload.requests)) {
        $query = ConvertTo-QueryString -Pairs @($requestSpec.params)
        $requestUrl = [string]$requestSpec.url
        if (-not [string]::IsNullOrEmpty($query)) {
            $requestUrl = "$requestUrl`?$query"
        }
        $started = [System.DateTimeOffset]::Now
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $response = $null
        $request = $null
        $cancellation = [System.Threading.CancellationTokenSource]::new($deadlineMs)
        try {
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
            $response = $client.SendAsync($request, $cancellation.Token).GetAwaiter().GetResult()
            $raw = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            $completed = [System.DateTimeOffset]::Now
            $stopwatch.Stop()
            $redirectChain = @()
            if ([int]$response.StatusCode -ge 300 -and [int]$response.StatusCode -lt 400) {
                if ($null -ne $response.Headers.Location) {
                    $redirectChain = @([string]$response.Headers.Location)
                }
            }
            if ($raw.Length -gt $maxResponseBytes) {
                throw [System.InvalidOperationException]::new("response_too_large")
            }
            $results.Add([ordered]@{
                endpoint_id = [string]$requestSpec.endpoint_id
                capability = [string]$requestSpec.capability
                url = [string]$requestSpec.url
                params = @($requestSpec.params)
                code = [string]$requestSpec.code
                started_at = $started.ToString("o")
                completed_at = $completed.ToString("o")
                elapsed_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
                http_status_code = [int]$response.StatusCode
                final_url = [string]$response.RequestMessage.RequestUri.AbsoluteUri
                redirect_chain = $redirectChain
                response_byte_count = [int]$raw.Length
                raw_hash = Get-Sha256Hex -Bytes $raw
                content_base64 = [Convert]::ToBase64String($raw)
                exception_type = ""
                exception_stage = ""
                innermost_exception_type = ""
                exception_message = ""
            })
        }
        catch {
            $completed = [System.DateTimeOffset]::Now
            $stopwatch.Stop()
            $empty = [byte[]]::new(0)
            $exception = $_.Exception
            $results.Add([ordered]@{
                endpoint_id = [string]$requestSpec.endpoint_id
                capability = [string]$requestSpec.capability
                url = [string]$requestSpec.url
                params = @($requestSpec.params)
                code = [string]$requestSpec.code
                started_at = $started.ToString("o")
                completed_at = $completed.ToString("o")
                elapsed_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
                http_status_code = $null
                final_url = ""
                redirect_chain = @()
                response_byte_count = 0
                raw_hash = Get-Sha256Hex -Bytes $empty
                content_base64 = ""
                exception_type = $exception.GetType().Name
                exception_stage = "http_request"
                innermost_exception_type = Get-InnermostExceptionType -Exception $exception
                exception_message = ([string]$exception.Message).Substring(
                    0,
                    [math]::Min(500, ([string]$exception.Message).Length)
                )
            })
        }
        finally {
            if ($null -ne $response) { $response.Dispose() }
            if ($null -ne $request) { $request.Dispose() }
            $cancellation.Dispose()
        }
    }
}
finally {
    $client.Dispose()
    $handler.Dispose()
}

[ordered]@{
    transport_id = "dotnet_httpclient_diagnostic_v1"
    request_count = $results.Count
    proxy_mode = [ordered]@{
        mode = $(if ($proxyBypassed) { "direct" } else { "windows_system_proxy" })
        configured = (-not $proxyBypassed)
        endpoints = $(if ($proxyEndpoint) { [ordered]@{ https = $proxyEndpoint } } else { [ordered]@{} })
    }
    results = @($results)
} | ConvertTo-Json -Depth 16 -Compress
