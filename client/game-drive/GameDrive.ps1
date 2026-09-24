# UNTESTED on the target Windows PCs and Ubuntu LIO NAS. Read README.md first.
# This script never initializes a disk, creates a partition, or formats a volume.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Status', 'Probe', 'Attach', 'Release')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open an elevated Windows PowerShell session.'
    }
}

function Assert-Value([string]$Value, [string]$Label, [string]$Pattern) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match 'REPLACE' -or $Value -notmatch $Pattern) {
        throw "$Label must be filled from the actual machines and match $Pattern"
    }
}

function Assert-IPv4([string]$Value, [string]$Label) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.ToString() -ne $Value -or $Value -eq '0.0.0.0') {
        throw "$Label must be a concrete IPv4 address."
    }
}

function Read-Config([string]$Path) {
    $config = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.HostName -notin @('desktop', 'laptop')) { throw 'HostName must be desktop or laptop.' }
    Assert-Value $config.TargetIqn 'TargetIqn' '^iqn\.[A-Za-z0-9.:-]+$'
    Assert-Value $config.PortalAddress 'PortalAddress' '^[A-Za-z0-9.:-]+$'
    Assert-Value $config.InitiatorAddress 'InitiatorAddress' '^\d{1,3}(\.\d{1,3}){3}$'
    Assert-IPv4 $config.PortalAddress 'PortalAddress'
    Assert-IPv4 $config.InitiatorAddress 'InitiatorAddress'
    if ([int]$config.MinimumLinkSpeedGbps -lt 1 -or [int]$config.MinimumLinkSpeedGbps -gt 100) {
        throw 'MinimumLinkSpeedGbps must be between 1 and 100.'
    }
    Assert-Value $config.ControllerSshHost 'ControllerSshHost' '^[A-Za-z0-9.:-]+$'
    Assert-Value $config.ControllerSshUser 'ControllerSshUser' '^[A-Za-z0-9_.-]+$'
    if ($config.ControllerScriptPath -cne '/usr/local/sbin/game-lun-controller.py') {
        throw 'ControllerScriptPath must be /usr/local/sbin/game-lun-controller.py'
    }
    if ([int]$config.PortalPort -lt 1 -or [int]$config.PortalPort -gt 65535) { throw 'Invalid PortalPort.' }
    if ($config.DriveLetter -notmatch '^[A-Z]$') { throw 'DriveLetter must be one uppercase letter.' }
    if ($config.ExpectedVolumeLabel -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid ExpectedVolumeLabel.' }
    $expectedSshUser = "gamehandoff-$($config.HostName)"
    if ($config.ControllerSshUser -cne $expectedSshUser) {
        throw "ControllerSshUser must be $expectedSshUser for this PC."
    }
    Assert-Value $config.ControllerSshIdentityFile 'ControllerSshIdentityFile' '^.+$'
    if (-not (Test-Path -LiteralPath $config.ControllerSshIdentityFile -PathType Leaf)) {
        throw "SSH private key is missing: $($config.ControllerSshIdentityFile)"
    }
    if (-not [string]::IsNullOrEmpty([string]$config.ChapUsername)) {
        throw 'CHAP is not configured on this target. Leave ChapUsername empty.'
    }
    return $config
}

function Assert-SelectedNetwork($Config) {
    $ip = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Config.InitiatorAddress -ErrorAction SilentlyContinue)
    if ($ip.Count -ne 1) { throw 'Configured InitiatorAddress is not assigned to exactly one local NIC.' }
    $adapter = Get-NetAdapter -InterfaceIndex $ip[0].InterfaceIndex -ErrorAction Stop
    if ($adapter.Status -ne 'Up') { throw 'Configured iSCSI network adapter is not connected.' }
    if ($adapter.LinkSpeed -notmatch '^([0-9]+(?:\.[0-9]+)?)\s+Gbps$') {
        throw "Cannot verify iSCSI link speed: $($adapter.LinkSpeed)"
    }
    if ([double]$Matches[1] -lt [double]$Config.MinimumLinkSpeedGbps) {
        throw "iSCSI link is $($adapter.LinkSpeed), below required $($Config.MinimumLinkSpeedGbps) Gbps."
    }
    Write-Host "iSCSI source: $($Config.InitiatorAddress) on $($adapter.Name), $($adapter.LinkSpeed)"
}

function Invoke-Controller($Config, [string]$Verb, [bool]$CleanOffline = $false) {
    $ssh = Get-Command ssh.exe -ErrorAction Stop
    $remote = "sudo -n /usr/bin/python3 $($Config.ControllerScriptPath) $Verb"
    if ($Verb -in @('claim', 'release')) { $remote += " --host $($Config.HostName)" }
    if ($CleanOffline) { $remote += ' --clean-client-offline' }
    $destination = "$($Config.ControllerSshUser)@$($Config.ControllerSshHost)"
    & $ssh.Source '-o' 'BatchMode=yes' '-o' 'StrictHostKeyChecking=yes' '-o' 'IdentitiesOnly=yes' '-o' 'ConnectTimeout=10' '-i' $Config.ControllerSshIdentityFile $destination $remote
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu LIO controller refused or failed $Verb. Ownership was not released." }
}

function Get-TargetSession($Config) {
    $sessions = @(Get-IscsiSession | Where-Object { $_.TargetNodeAddress -eq $Config.TargetIqn })
    if ($sessions.Count -gt 1) { throw 'More than one local session exists for the game target.' }
    if ($sessions.Count -eq 0) { return $null }
    if ($sessions[0].PSObject.Properties.Name -notcontains 'IsPersistent') {
        throw 'Cannot verify whether the iSCSI session is persistent; refusing handoff.'
    }
    if ($sessions[0].IsPersistent) {
        throw 'This session is persistent. Unregister it before continuing; automatic reconnect breaks exclusive handoff.'
    }
    if (-not $sessions[0].IsConnected) { throw 'The game target session is not connected.' }
    $connections = @(Get-IscsiConnection -IscsiSession $sessions[0])
    if ($connections.Count -ne 1 -or
        $connections[0].InitiatorAddress -ne $Config.InitiatorAddress -or
        $connections[0].TargetAddress -ne $Config.PortalAddress -or
        [int]$connections[0].TargetPortNumber -ne [int]$Config.PortalPort) {
        throw 'Existing game target session does not use exactly the configured iSCSI network path.'
    }
    return $sessions[0]
}

function Get-TargetDisk($Session) {
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $disks = @(Get-Disk -iSCSISession $Session)
        if ($disks.Count -eq 1) {
            if ($disks[0].BusType -ne 'iSCSI') { throw 'Associated disk is not reported as iSCSI.' }
            return $disks[0]
        }
        if ($disks.Count -gt 1) { throw 'Target has more than one disk; refusing to select one.' }
        Start-Sleep -Seconds 1
    }
    throw 'No disk appeared for the target session.'
}

function Assert-DiskIdentity($Config, $Disk) {
    Assert-Value $Config.ExpectedDiskUniqueId 'ExpectedDiskUniqueId' '^.+$'
    if ($Disk.UniqueId -ne $Config.ExpectedDiskUniqueId) {
        throw "Disk UniqueId mismatch. Expected $($Config.ExpectedDiskUniqueId), found $($Disk.UniqueId)."
    }
    if ([double]$Config.ExpectedSizeBytes -le 0 -or [int64]$Disk.Size -ne [int64]$Config.ExpectedSizeBytes) {
        throw "Disk size mismatch. Expected $($Config.ExpectedSizeBytes), found $($Disk.Size)."
    }
}

function Ensure-TargetConnection($Config) {
    Assert-SelectedNetwork $Config
    $service = Get-Service -Name MSiSCSI -ErrorAction Stop
    if ($service.Status -ne 'Running') { Start-Service -Name MSiSCSI }
    $session = Get-TargetSession $Config
    if ($null -ne $session) { return $session }

    $portal = @(Get-IscsiTargetPortal | Where-Object {
        $_.TargetPortalAddress -eq $Config.PortalAddress -and
        [int]$_.TargetPortalPortNumber -eq [int]$Config.PortalPort
    })
    if ($portal.Count -eq 0) {
        New-IscsiTargetPortal -TargetPortalAddress $Config.PortalAddress -TargetPortalPortNumber ([uint16]$Config.PortalPort) -InitiatorPortalAddress $Config.InitiatorAddress | Out-Null
    } else {
        Update-IscsiTargetPortal -TargetPortalAddress $Config.PortalAddress -TargetPortalPortNumber ([uint16]$Config.PortalPort) -InitiatorPortalAddress $Config.InitiatorAddress | Out-Null
    }
    $target = @(Get-IscsiTarget -NodeAddress $Config.TargetIqn)
    if ($target.Count -ne 1) { throw 'The discovered portal does not expose exactly the configured target IQN.' }

    $connectionArgs = @{
        NodeAddress = $Config.TargetIqn
        TargetPortalAddress = $Config.PortalAddress
        TargetPortalPortNumber = [uint16]$Config.PortalPort
        InitiatorPortalAddress = $Config.InitiatorAddress
        IsPersistent = $false
    }
    $connectionArgs.AuthenticationType = 'NONE'
    Connect-IscsiTarget @connectionArgs | Out-Null
    $session = Get-TargetSession $Config
    if ($null -eq $session) { throw 'Connection did not produce a target session.' }
    return $session
}

Assert-Administrator
$config = Read-Config $ConfigPath
if ($Action -eq 'Status') {
    Invoke-Controller $config 'status'
    $service = Get-Service -Name MSiSCSI -ErrorAction Stop
    if ($service.Status -eq 'Running') {
        $session = Get-TargetSession $config
        if ($null -ne $session) {
            $disk = Get-TargetDisk $session
            $disk | Select-Object Number, UniqueId, Size, IsOffline, PartitionStyle
        }
    }
    return
}
if ($Action -in @('Probe', 'Attach')) {
    if ($Action -eq 'Attach') {
        Assert-Value $config.ExpectedDiskUniqueId 'ExpectedDiskUniqueId' '^.+$'
        if ([double]$config.ExpectedSizeBytes -le 0) { throw 'ExpectedSizeBytes is required for Attach.' }
    }
    # Fail locally before reserving the server target. A slow/missing link or
    # preexisting persistent iSCSI session would otherwise leave a stale claim.
    Assert-SelectedNetwork $config
    $service = Get-Service -Name MSiSCSI -ErrorAction Stop
    if ($service.Status -ne 'Running') { Start-Service -Name MSiSCSI }
    Get-TargetSession $config | Out-Null
    Invoke-Controller $config 'claim'
    $session = Ensure-TargetConnection $config
    $disk = Get-TargetDisk $session
    if ($Action -eq 'Probe') {
        Write-Host 'PROBE ONLY: no disk was brought online or formatted by this script.'
        $disk | Select-Object Number, UniqueId, Size, IsOffline, PartitionStyle
        Write-Host 'Record UniqueId and Size in this PC config. Then use the manual first-use procedure in README.md.'
        return
    }
    Assert-DiskIdentity $config $disk
    if ($disk.PartitionStyle -eq 'RAW') { throw 'LUN is uninitialized; complete the manual first-use procedure first.' }
    if ($disk.IsOffline) { Set-Disk -Number $disk.Number -IsOffline $false }
    $disk = Get-Disk -Number $disk.Number
    if ($disk.IsOffline) { throw 'Disk remained offline.' }
    $volumePartitions = @(Get-Partition -DiskNumber $disk.Number | Where-Object {
        $volume = $_ | Get-Volume -ErrorAction SilentlyContinue
        $volume -and $volume.FileSystem -eq 'NTFS' -and $volume.FileSystemLabel -eq $config.ExpectedVolumeLabel
    })
    if ($volumePartitions.Count -ne 1) { throw 'Expected exactly one NTFS game volume with the configured label.' }
    $partition = $volumePartitions[0]
    $occupied = @(Get-Partition -DriveLetter $config.DriveLetter -ErrorAction SilentlyContinue)
    if ($occupied.Count -gt 0 -and ($occupied[0].DiskNumber -ne $disk.Number -or $occupied[0].PartitionNumber -ne $partition.PartitionNumber)) {
        throw "Drive letter $($config.DriveLetter): is occupied by another disk."
    }
    if ($partition.DriveLetter -ne $config.DriveLetter) {
        Set-Partition -DiskNumber $disk.Number -PartitionNumber $partition.PartitionNumber -NewDriveLetter $config.DriveLetter
    }
    Write-Host "Game volume is available as $($config.DriveLetter): on $($config.HostName)."
    return
}

# Release never calls the server if the local session or exact disk identity is
# missing. This keeps a crashed/disconnected owner from being silently cleared.
$session = Get-TargetSession $config
if ($null -eq $session) { throw 'No local game-target session exists; crash recovery requires manual inspection.' }
$disk = Get-TargetDisk $session
Assert-DiskIdentity $config $disk
if (Get-Process -Name steam, steamwebhelper -ErrorAction SilentlyContinue) {
    throw 'Close Steam fully and stop the game before releasing the LUN.'
}
$confirmation = Read-Host "Close every game and process using $($config.DriveLetter):, then type RELEASE $($disk.UniqueId)"
if ($confirmation -cne "RELEASE $($disk.UniqueId)") {
    throw 'Release confirmation did not match. Server ownership remains held.'
}
if (-not $disk.IsOffline) { Set-Disk -Number $disk.Number -IsOffline $true }
$disk = Get-Disk -Number $disk.Number
if (-not $disk.IsOffline) { throw 'Disk did not go offline; server ownership remains held.' }
Disconnect-IscsiTarget -NodeAddress $config.TargetIqn -Confirm:$false | Out-Null
if ($null -ne (Get-TargetSession $config)) { throw 'Local iSCSI session still exists; server ownership remains held.' }
Invoke-Controller $config 'release' $true
Write-Host 'The server fenced the old initiator and released ownership.'
