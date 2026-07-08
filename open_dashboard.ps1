$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url = "http://localhost:8501/"
$Port = 8501
$StdoutLog = Join-Path $Root "dashboard_stdout.log"
$StderrLog = Join-Path $Root "dashboard_stderr.log"

function Test-DashboardPort {
    param([int]$Port)
    try {
        $Client = New-Object System.Net.Sockets.TcpClient
        $Async = $Client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $Async.AsyncWaitHandle.WaitOne(500, $false)) {
            $Client.Close()
            return $false
        }
        $Client.EndConnect($Async)
        $Client.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-DashboardPortOwners {
    param([int]$Port)
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-DashboardPortOwners {
    param([int]$Port)
    foreach ($Owner in (Get-DashboardPortOwners -Port $Port)) {
        if ($Owner) {
            Stop-Process -Id $Owner -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-DashboardProcesses {
    param([string]$Root)
    $RootPattern = [Regex]::Escape($Root)
    $Processes = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "python*" -and
        ($_.CommandLine -match "overnight_quant[\\/]scripts[\\/]run_dashboard.py" -or
         ($_.CommandLine -match $RootPattern -and $_.CommandLine -match "overnight_quant[\\/]ui[\\/]dashboard.py"))
    }
    foreach ($Process in $Processes) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Test-DashboardProcessFresh {
    param([int]$Port, [string]$Root)
    $Owners = Get-DashboardPortOwners -Port $Port
    if (-not $Owners -or $Owners.Count -eq 0) {
        return $false
    }
    $RootPattern = [Regex]::Escape($Root)
    $WatchedFiles = @(
        (Join-Path $Root "overnight_quant\ui\dashboard.py"),
        (Join-Path $Root "overnight_quant\ui\result_parser.py"),
        (Join-Path $Root "overnight_quant\scripts\run_dashboard.py")
    )
    $NewestCodeWrite = ($WatchedFiles | Where-Object { Test-Path $_ } | ForEach-Object { (Get-Item $_).LastWriteTime } | Sort-Object -Descending | Select-Object -First 1)
    foreach ($Owner in $Owners) {
        $Process = Get-Process -Id $Owner -ErrorAction SilentlyContinue
        $CimProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$Owner" -ErrorAction SilentlyContinue
        $CommandLine = (($CimProcess | Select-Object -ExpandProperty CommandLine) -as [string])
        if (-not $CommandLine -or -not ($CommandLine -match $RootPattern -and $CommandLine -match "overnight_quant[\\/]ui[\\/]dashboard.py")) {
            return $false
        }
        if ($NewestCodeWrite -and $Process -and $Process.StartTime -lt $NewestCodeWrite) {
            return $false
        }
    }
    return $true
}

if ((Test-DashboardPort -Port $Port) -and -not (Test-DashboardProcessFresh -Port $Port -Root $Root)) {
    Stop-DashboardProcesses -Root $Root
    Stop-DashboardPortOwners -Port $Port
    Start-Sleep -Seconds 2
}

if (-not (Test-DashboardPort -Port $Port)) {
    Stop-DashboardProcesses -Root $Root
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        $Python = "python"
    }

    Start-Process `
        -FilePath $Python `
        -ArgumentList @("overnight_quant\scripts\run_dashboard.py") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog

    for ($i = 0; $i -lt 60; $i++) {
        if (Test-DashboardPort -Port $Port) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
}

Start-Process $Url
