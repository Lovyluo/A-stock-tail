param(
    [string]$ShareName = "A-stock",
    [string]$Path = "D:\A-stock",
    [switch]$Writable
)

$ErrorActionPreference = "Stop"
$logPath = Join-Path $Path "lan_share_setup.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Output $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Please run this script in an elevated PowerShell window."
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "Share path does not exist: $Path"
}

$everyone = (New-Object System.Security.Principal.SecurityIdentifier "S-1-1-0").Translate([System.Security.Principal.NTAccount]).Value
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Write-Log "Configuring LAN share '$ShareName' for $Path"
Write-Log "Current user: $currentUser"
Write-Log "Everyone principal: $everyone"

$serverService = Get-Service LanmanServer
if ($serverService.Status -ne "Running") {
    Start-Service LanmanServer
}

Get-NetConnectionProfile |
    Where-Object { $_.NetworkCategory -eq "Public" } |
    ForEach-Object {
        Write-Log "Setting network profile '$($_.Name)' / '$($_.InterfaceAlias)' to Private"
        Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
    }

Write-Log "Enabling File and Printer Sharing firewall rules for Private profile"
Get-NetFirewallRule -DisplayGroup "File and Printer Sharing" -ErrorAction SilentlyContinue |
    Where-Object { $_.Profile.ToString() -match "Private|Any" } |
    Enable-NetFirewallRule

$existing = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Log "Existing share '$ShareName' found at '$($existing.Path)'"
    if ($existing.Path -ne $Path) {
        throw "Share name '$ShareName' already exists for a different path: $($existing.Path)"
    }
} else {
    Write-Log "Creating SMB share '$ShareName'"
    if ($Writable) {
        New-SmbShare -Name $ShareName -Path $Path -FullAccess $currentUser -ChangeAccess $everyone -Description "A-stock project LAN share" | Out-Null
    } else {
        New-SmbShare -Name $ShareName -Path $Path -FullAccess $currentUser -ReadAccess $everyone -Description "A-stock project LAN share" | Out-Null
    }
}

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -ExpandProperty IPAddress

Write-Log "Share ready."
Write-Log "UNC paths:"
Write-Log "  \\$env:COMPUTERNAME\$ShareName"
foreach ($ip in $ips) {
    Write-Log "  \\$ip\$ShareName"
}

Get-SmbShare -Name $ShareName | Format-List Name,Path,Description,FolderEnumerationMode,CachingMode
Get-SmbShareAccess -Name $ShareName | Format-Table -AutoSize
