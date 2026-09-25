#Requires -Version 5.1
<#
Additive setup for the MSI laptop. Its existing C:\AI native llama.cpp,
Switchboard, models, recorder, GPU power choices, and startup tasks are left
untouched. Steam iSCSI attachment is handled by client\game-drive\GameDrive.ps1 later.
#>
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'InstallTools', 'Verify')]
    [string]$Action = 'Plan',
    [string]$HpIp = '',
    [string]$DellIp = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Windows11 {
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.Caption -notmatch 'Windows 11') { throw "Windows 11 expected; found $($os.Caption)" }
}

function Install-ByWinget {
    param([string]$Id)
    $winget = Get-Command winget.exe -ErrorAction Stop
    & $winget.Source list --id $Id --exact --accept-source-agreements --disable-interactivity | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$Id is already installed; preserving its version."
        return
    }
    & $winget.Source install --id $Id --exact --source winget --no-upgrade `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "WinGet failed to install or identify $Id (exit $LASTEXITCODE)." }
}

Assert-Windows11

switch ($Action) {
    'Plan' {
        Write-Host "Computer: $env:COMPUTERNAME"
        Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed | Format-Table | Out-Host
        if (Test-Path -LiteralPath 'C:\AI' -PathType Container) {
            Write-Host 'C:\AI present; this script leaves it unchanged.'
        }
        if (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue) {
            & nvidia-smi.exe --query-gpu=name,memory.total,driver_version --format=csv,noheader
        }
        Write-Host 'InstallTools adds Git, VS Code, PowerShell 7, uv and Steam only when absent.'
    }
    'InstallTools' {
        foreach ($id in @('Git.Git', 'Microsoft.VisualStudioCode', 'Microsoft.PowerShell', 'astral-sh.uv', 'Valve.Steam')) {
            Install-ByWinget $id
        }
        Write-Host 'Tools installed or already present. Restart the terminal to pick up PATH changes.'
        Write-Host 'Existing native llama.cpp and C:\AI remain unchanged.'
        if ($HpIp -and $DellIp) {
            & (Join-Path (Split-Path -Parent $PSScriptRoot) 'client\Install-Control-Center.ps1') -Role laptop -HpIp $HpIp -DellIp $DellIp
        } else {
            Write-Host 'Next run client\Install-Control-Center.ps1 -Role laptop -HpIp HP_IP -DellIp DELL_IP to create the desktop app shortcut.'
        }
    }
    'Verify' {
        Get-NetAdapter | Select-Object Name, Status, LinkSpeed | Format-Table | Out-Host
        foreach ($command in @('git.exe', 'code.cmd', 'pwsh.exe', 'uv.exe', 'ssh.exe')) {
            $found = Get-Command $command -ErrorAction SilentlyContinue
            Write-Host "$command : $([bool]$found)"
        }
        $iscsi = Get-Service MSiSCSI -ErrorAction SilentlyContinue
        if ($iscsi) { Write-Host "Microsoft iSCSI service: $($iscsi.Status)" }
        Write-Host 'Use client\game-drive\GameDrive.ps1 for the exclusive S: game library once the NAS exists.'
    }
}
