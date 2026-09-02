function Get-SourceProxyReadiness {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [scriptblock]$ResolveProxy = {
            param([uri]$Destination)
            $proxy = [Net.WebRequest]::GetSystemWebProxy()
            return $proxy.GetProxy($Destination)
        },

        [scriptblock]$TestLocalListener = {
            param([string]$HostName, [int]$Port)
            $client = [Net.Sockets.TcpClient]::new()
            try {
                $pending = $client.BeginConnect($HostName, $Port, $null, $null)
                if (-not $pending.AsyncWaitHandle.WaitOne(250)) {
                    return $false
                }
                $client.EndConnect($pending)
                return $true
            }
            catch {
                return $false
            }
            finally {
                $client.Dispose()
            }
        }
    )

    $normalizedSource = ([string]$Source).Trim().ToLowerInvariant()
    $base = [ordered]@{
        source = $normalizedSource
        execution_ok = $true
        data_ready = $false
        source_allowed = $true
        proxy_in_use = $false
        local_proxy = $false
        proxy_endpoint = ''
        candidates = @()
        tickets = @()
        orders = @()
    }
    if ($normalizedSource -ne 'eastmoney') {
        $base.status = 'SOURCE_PROXY_CHECK_NOT_REQUIRED'
        return $base
    }

    $destination = [uri]'https://push2.eastmoney.com/'
    try {
        $resolved = & $ResolveProxy $destination
        $proxyUri = if ($resolved -is [uri]) {
            $resolved
        }
        else {
            [uri]([string]$resolved)
        }
    }
    catch {
        $base.status = 'SOURCE_PROXY_CHECK_FAILED'
        $base.execution_ok = $false
        $base.source_allowed = $false
        return $base
    }

    if ($null -eq $proxyUri -or $proxyUri.AbsoluteUri -eq $destination.AbsoluteUri) {
        $base.status = 'SOURCE_PROXY_NOT_USED'
        return $base
    }

    $base.proxy_in_use = $true
    $hostName = ([string]$proxyUri.Host).Trim().ToLowerInvariant()
    $isLocal = $hostName -in @('127.0.0.1', 'localhost', '::1')
    $base.local_proxy = $isLocal
    if (-not $isLocal) {
        $base.status = 'SOURCE_REMOTE_PROXY_NOT_CHECKED'
        return $base
    }

    $base.proxy_endpoint = "${hostName}:$($proxyUri.Port)"
    $listening = $false
    try {
        $listening = [bool](& $TestLocalListener $hostName $proxyUri.Port)
    }
    catch {
        $listening = $false
    }
    if ($listening) {
        $base.status = 'SOURCE_LOCAL_PROXY_READY'
        return $base
    }

    $base.status = 'SOURCE_LOCAL_PROXY_UNAVAILABLE'
    $base.source_allowed = $false
    return $base
}
