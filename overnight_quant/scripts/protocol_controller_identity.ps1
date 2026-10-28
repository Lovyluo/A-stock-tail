# Inspector-only role checks. This file never launches or authorizes a controller.
function ConvertFrom-ControllerCommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { throw 'COMMAND_LINE_UNAVAILABLE' }
    if (!('AStockController.NativeArgv' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace AStockController {
    public static class NativeArgv {
        [DllImport("shell32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
        public static extern IntPtr CommandLineToArgvW(string command, out int count);
        [DllImport("kernel32.dll")]
        public static extern IntPtr LocalFree(IntPtr memory);
    }
}
'@
    }
    $count = 0
    $memory = [AStockController.NativeArgv]::CommandLineToArgvW($CommandLine,[ref]$count)
    if ($memory -eq [IntPtr]::Zero) { throw 'COMMAND_LINE_PARSE_FAILED' }
    try {
        $values = @()
        for ($i=0; $i -lt $count; $i++) {
            $pointer = [Runtime.InteropServices.Marshal]::ReadIntPtr($memory,$i * [IntPtr]::Size)
            $values += [Runtime.InteropServices.Marshal]::PtrToStringUni($pointer)
        }
        return ,$values
    } finally { $null = [AStockController.NativeArgv]::LocalFree($memory) }
}
function Get-ControllerPath([string]$Path,[string]$Directory) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'EXECUTABLE_PATH_UNAVAILABLE' }
    if (![IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Directory $Path }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}
function Get-ControllerEntry($Arguments,[string]$Directory) {
    $i = 1
    while ($i -lt $Arguments.Count -and $Arguments[$i] -in @('-u','-B','-E','-s','-I')) { $i++ }
    if ($i -ge $Arguments.Count) { return @{kind='missing';value=''} }
    if ($Arguments[$i] -eq '-m') {
        if ($i+1 -ge $Arguments.Count) { return @{kind='missing';value=''} }
        return @{kind='module';value=$Arguments[$i+1]}
    }
    if ($Arguments[$i].StartsWith('-m') -and $Arguments[$i].Length -gt 2) {
        return @{kind='module';value=$Arguments[$i].Substring(2)}
    }
    if ($Arguments[$i].StartsWith('-')) { return @{kind='option';value=$Arguments[$i]} }
    return @{kind='file';value=(Get-ControllerPath $Arguments[$i] $Directory)}
}
function Resolve-ProtocolControllers($Processes,[int]$ControllerPid,[string]$PythonPath,
        [string]$BasePythonPath,[string]$Directory,[string]$ProjectRoot) {
    $script:controllerDiagnostics = @()
    $allowed = @(); $boundary = @(); $seen = @()
    if ([string]::IsNullOrWhiteSpace($Directory) -or ![IO.Path]::IsPathRooted($Directory) -or
            [string]::IsNullOrWhiteSpace($PythonPath) -or [string]::IsNullOrWhiteSpace($BasePythonPath)) {
        $script:controllerDiagnostics = @(@{stage='configuration';pid=$ControllerPid;parent_pid=$null
            process_name=$null;failed_item='RUNTIME_IDENTITY_INPUT_MISSING';classification='REJECTED'})
        throw 'Protocol controller identity rejected: configuration:RUNTIME_IDENTITY_INPUT_MISSING'
    }
    $expectedEntry = Get-ControllerPath (Join-Path $ProjectRoot 'overnight_quant/scripts/run_minute_protocol_diagnostic.py') $ProjectRoot
    $python = Get-ControllerPath $PythonPath $Directory
    $basePython = Get-ControllerPath $BasePythonPath $Directory
    $current = $ControllerPid; $childArguments = $null; $childExecutable = $null
    for ($depth=0; $depth -lt 3; $depth++) {
        $row = @($Processes | Where-Object { $_.ProcessId -eq $current })
        $diagnostic = [ordered]@{stage=if($depth -eq 0){'controller'}else{'parent'};pid=$current
            parent_pid=$null;process_name=$null;failed_item=$null;classification='UNKNOWN'
            command_line_sha256=$null;entry_kind=$null;entry_name=$null}
        try {
            if ($row.Count -ne 1) { throw 'PROCESS_UNAVAILABLE' }
            if ($depth -eq 0) {
                $inspector = @($Processes | Where-Object { $_.ProcessId -eq $PID })
                if ($inspector.Count -ne 1 -or $inspector[0].ParentProcessId -ne $ControllerPid) {
                    throw 'CONTROLLER_NOT_INSPECTOR_PARENT'
                }
            }
            if ($current -in $seen) { throw 'PARENT_CYCLE' }
            $seen += $current; $item = $row[0]
            $diagnostic.parent_pid = [int]$item.ParentProcessId
            $diagnostic.process_name = [string]$item.Name
            if ([string]::IsNullOrWhiteSpace($item.Name)) { throw 'PROCESS_NAME_UNAVAILABLE' }
            $executable = Get-ControllerPath $item.ExecutablePath $Directory
            if ([IO.Path]::GetFileName($executable) -ine $item.Name) { throw 'IMAGE_NAME_PATH_MISMATCH' }
            $arguments = ConvertFrom-ControllerCommandLine $item.CommandLine
            $algorithm = [Security.Cryptography.SHA256]::Create()
            try { $diagnostic.command_line_sha256 = ([BitConverter]::ToString($algorithm.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes($item.CommandLine)))).Replace('-','').ToLowerInvariant() }
            finally { $algorithm.Dispose() }
            $entry = Get-ControllerEntry $arguments $Directory
            $diagnostic.entry_kind = $entry.kind
            $diagnostic.entry_name = if($entry.kind -eq 'file'){
                if($entry.value -ieq $expectedEntry){'run_minute_protocol_diagnostic.py'}else{'OTHER_FILE'}
            }else{$null}
            $isPython = $item.Name -match '^python(w)?\.exe$'
            $isEntry = ($entry.kind -eq 'file' -and $entry.value -ieq $expectedEntry) -or
                ($entry.kind -eq 'module' -and $entry.value -ceq 'overnight_quant.scripts.run_minute_protocol_diagnostic' -and
                    (Get-ControllerPath $Directory $Directory) -ieq (Get-ControllerPath $ProjectRoot $Directory))
            if ($depth -gt 0 -and (!$isPython -or !$isEntry)) {
                # Stop at an ordinary launch parent; it is NOT an allowed controller.
                $directWorker = $entry.kind -eq 'file' -and [IO.Path]::GetFileName($entry.value) -match
                    '^(minute_.*worker|run_minute_.*|market_source_worker|run_market_source_validation|run_complete_industry_audit)\.(py|ps1)$'
                if ($entry.kind -eq 'module' -and $entry.value -match '^overnight_quant\.(data\.(minute_.*worker|market_source_worker)|scripts\.(run_minute_.*|run_market_source_validation|run_complete_industry_audit))$') {
                    $directWorker = $true
                }
                if ($item.Name -match '^(powershell|pwsh)\.exe$' -and '-File' -in $arguments) {
                    $fileIndex = 0
                    while ($arguments[$fileIndex] -ine '-File') { $fileIndex++ }
                    if ($fileIndex+1 -ge $arguments.Count) { throw 'PARENT_FILE_ARGUMENT_UNAVAILABLE' }
                    $directWorker = [IO.Path]::GetFileName($arguments[$fileIndex+1]) -match '^(minute_.*|run_minute_.*|market_.*worker|run_market_source_validation|run_complete_industry_audit)\.(py|ps1)$'
                }
                if ($directWorker) { throw 'PARENT_IS_PROJECT_WORKER' }
                $diagnostic.classification = 'ORDINARY_LAUNCH_BOUNDARY_NOT_CONTROLLER'
                $script:controllerDiagnostics += [pscustomobject]$diagnostic
                if ($item.Name -match '^(python(w)?|powershell|pwsh)\.exe$') { $boundary += $current }
                return @{allowed=$allowed;ordinary_boundary=$boundary;diagnostics=$script:controllerDiagnostics}
            }
            if (!$isPython) { throw 'PROCESS_NAME_NOT_PYTHON' }
            if ($executable -ine $python -and $executable -ine $basePython) { throw 'EXECUTABLE_PATH_MISMATCH' }
            if (!$isEntry) { throw 'CONTROLLER_ENTRY_MISMATCH' }
            if ('--network' -cnotin $arguments -or '--validate-only' -cin $arguments -or '--verify' -cin $arguments) {
                throw 'NETWORK_MODE_ARGUMENT_MISMATCH'
            }
            if ($depth -gt 0 -and ($depth -ne 1 -or $childExecutable -ine $basePython -or
                    $executable -ine $python -or $python -ieq $basePython -or
                    (ConvertTo-Json -InputObject @($arguments | Select-Object -Skip 1) -Compress) -cne
                    (ConvertTo-Json -InputObject @($childArguments | Select-Object -Skip 1) -Compress))) {
                throw 'UNPROVEN_PYTHON_ANCESTOR_CHAIN'
            }
            $diagnostic.classification = if($depth -eq 0){'ACTUAL_CONTROLLER'}else{'PROVEN_VENV_REDIRECTOR'}
            $script:controllerDiagnostics += [pscustomobject]$diagnostic
            $allowed += $current; $childArguments=$arguments; $childExecutable=$executable
            $current=[int]$item.ParentProcessId
        } catch {
            $diagnostic.failed_item=if($_.Exception.Message -cmatch '^[A-Z][A-Z_]+$'){$_.Exception.Message}else{'IDENTITY_QUERY_OR_PARSE_ERROR'}
            $diagnostic.classification='REJECTED'
            $script:controllerDiagnostics += [pscustomobject]$diagnostic
            throw ('Protocol controller identity rejected: '+$diagnostic.stage+':'+$diagnostic.failed_item)
        }
    }
    throw 'Protocol controller identity rejected: parent:CHAIN_DEPTH_EXCEEDED'
}
