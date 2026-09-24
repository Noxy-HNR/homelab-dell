#Requires -Version 5.1
<#
Creates this PC's SSH key and local game-client.json. It does not connect to,
initialize, format, or attach the iSCSI disk.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('desktop', 'laptop')][string]$HostRole,
    [Parameter(Mandatory = $true)][string]$HpIp,
    [Parameter(Mandatory = $true)][string]$LocalIp,
    [string]$SshHost = $HpIp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this helper in elevated PowerShell so it can start the Windows iSCSI service.'
}

function Assert-IPv4([string]$Value, [string]$Label) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.ToString() -ne $Value -or $Value -eq '0.0.0.0') {
        throw "$Label must be an IPv4 address assigned to the planned LAN."
    }
}

Assert-IPv4 $HpIp 'HpIp'
Assert-IPv4 $LocalIp 'LocalIp'
$address = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $LocalIp -ErrorAction SilentlyContinue)
if ($address.Count -ne 1) { throw "LocalIp $LocalIp is not assigned to exactly one network adapter." }
$adapter = Get-NetAdapter -InterfaceIndex $address[0].InterfaceIndex -ErrorAction Stop
if ($adapter.Status -ne 'Up' -or $adapter.LinkSpeed -notmatch '^10(?:\.0)?\s+Gbps$') {
    throw "The selected adapter must be connected at 10 Gbps; found $($adapter.Name): $($adapter.Status), $($adapter.LinkSpeed)."
}
$iscsiService = Get-Service -Name MSiSCSI -ErrorAction Stop
if ($iscsiService.Status -ne 'Running') { Start-Service -Name MSiSCSI }
$ports = @(Get-InitiatorPort -ErrorAction Stop | Where-Object { $_.NodeAddress -match '^iqn\.' })
if ($ports.Count -ne 1) {
    throw "Expected one Windows iSCSI initiator IQN; found $($ports.Count). Record the correct IQN manually if this PC has several."
}

$sshKeygen = Get-Command ssh-keygen.exe -ErrorAction Stop
$sshDir = Join-Path $env:USERPROFILE '.ssh'
New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
$keyPath = Join-Path $sshDir "homelab_game_$HostRole"
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    if ($PSVersionTable.PSVersion.Major -eq 5) {
        # Windows PowerShell 5.1 needs quoted empty text for native programs.
        & $sshKeygen.Source -t ed25519 -N '""' -f $keyPath -C "homelab-game-$HostRole" | Out-Null
    } elseif ($PSVersionTable.PSVersion -ge [version]'7.3') {
        & $sshKeygen.Source -t ed25519 -N '' -f $keyPath -C "homelab-game-$HostRole" | Out-Null
    } else {
        throw 'Use Windows PowerShell 5.1 (powershell.exe) or PowerShell 7.3+ to create an unencrypted SSH key.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'SSH key generation failed.' }
}
if (-not (Test-Path -LiteralPath "$keyPath.pub" -PathType Leaf)) {
    throw "SSH public key is missing: $keyPath.pub"
}

$configPath = Join-Path $PSScriptRoot 'game-client.json'
if (Test-Path -LiteralPath $configPath) {
    throw "Existing $configPath was preserved. Edit it manually if you need new addresses."
}
$config = [ordered]@{
    HostName = $HostRole
    TargetIqn = 'iqn.2026-09.local.homelab:steam-games'
    PortalAddress = $HpIp
    PortalPort = 3260
    InitiatorAddress = $LocalIp
    MinimumLinkSpeedGbps = 10
    ExpectedDiskUniqueId = 'REPLACE_AFTER_PROBE'
    ExpectedSizeBytes = 0
    ExpectedVolumeLabel = 'SteamGames'
    DriveLetter = 'S'
    ChapUsername = ''
    ControllerSshHost = $SshHost
    ControllerSshUser = "gamehandoff-$HostRole"
    ControllerSshIdentityFile = $keyPath
    ControllerScriptPath = '/usr/local/sbin/game-lun-controller.py'
}
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8
Write-Host "Created $configPath"
Write-Host "This PC's initiator IQN: $($ports[0].NodeAddress)"
Write-Host "This PC's SSH public key: $keyPath.pub"
Write-Host 'Copy the IQN into the HP controller config and the .pub file to the HP. Never copy the private key.'
