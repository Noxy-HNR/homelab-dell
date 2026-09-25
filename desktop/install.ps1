#Requires -Version 5.1
<#
Budget desktop setup. Keeps the GTX 1050 Ti and existing Windows installation.
Run Plan, then Install, reboot/finish WSL first launch if requested, then PullModel.
No disk is initialized, no iSCSI session is opened, and Ollama is not exposed on LAN.
#>
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Install', 'PullModel', 'Verify')]
    [string]$Action = 'Plan',
    [string]$ModelDirectory = 'C:\AI\OllamaModels',
    [string]$HpIp = '',
    [string]$DellIp = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Windows11 {
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.Caption -notmatch 'Windows 11') { throw "Windows 11 expected; found $($os.Caption)" }
}

function Assert-Admin {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run Install in elevated PowerShell.'
    }
}

function Install-ByWinget([string]$Id) {
    $winget = Get-Command winget.exe -ErrorAction Stop
    & $winget.Source list --id $Id --exact --accept-source-agreements --disable-interactivity | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$Id is already installed; preserving its version."
        return
    }
    & $winget.Source install --id $Id --exact --source winget --no-upgrade --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "WinGet failed on $Id (exit $LASTEXITCODE)." }
}

function Get-NvidiaVramMiB {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $smi) { return 0 }
    $lines = @(& $smi.Source --query-gpu=memory.total --format=csv,noheader,nounits 2>$null)
    $values = @($lines | ForEach-Object { if ($_ -match '^\s*(\d+)\s*$') { [int]$Matches[1] } })
    if ($values.Count -eq 0) { return 0 }
    return ($values | Measure-Object -Maximum).Maximum
}

function Get-OllamaExe {
    $found = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($path in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    throw 'Ollama is unavailable. Run Install and launch Ollama from Start.'
}

function Assert-LocalModelPath([string]$Path) {
    if ($Path.StartsWith('\\') -or $Path -match '^[Ss]:') {
        throw 'Keep AI models on a local client disk, not the iSCSI S: game drive or a UNC share.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    $drive = [IO.DriveInfo]::new($root)
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) {
        throw 'The Ollama model path must be on a fixed local client drive.'
    }
    if (-not $drive.IsReady -or $drive.AvailableFreeSpace -lt 6GB) {
        throw 'The local model drive needs at least 6 GiB free.'
    }
    return $full
}

Assert-Windows11
switch ($Action) {
    'Plan' {
        Write-Host "Computer: $env:COMPUTERNAME"
        Get-CimInstance Win32_VideoController | Select-Object Name | Format-Table | Out-Host
        Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed | Format-Table | Out-Host
        Write-Host "Detected NVIDIA VRAM: $(Get-NvidiaVramMiB) MiB"
        Write-Host "Proposed local Ollama model path: $ModelDirectory"
        Write-Host 'Install adds Git, VS Code, PowerShell 7, uv, Steam, Ollama, Docker Desktop and WSL Ubuntu 24.04.'
    }
    'Install' {
        Assert-Admin
        $old = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
        if ([string]::IsNullOrWhiteSpace($old)) {
            $path = Assert-LocalModelPath $ModelDirectory
            New-Item -ItemType Directory -Path $path -Force | Out-Null
            [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $path, 'User')
            Write-Host "Set user OLLAMA_MODELS=$path"
        } else {
            Write-Host "Preserving existing user OLLAMA_MODELS=$old"
        }
        foreach ($id in @('Git.Git', 'Microsoft.VisualStudioCode', 'Microsoft.PowerShell',
                          'astral-sh.uv', 'Valve.Steam', 'Ollama.Ollama', 'Docker.DockerDesktop')) {
            Install-ByWinget $id
        }
        $distros = @(& wsl.exe --list --quiet 2>$null)
        if (-not ($distros -match 'Ubuntu-24.04')) {
            & wsl.exe --install -d Ubuntu-24.04 --no-launch
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'WSL installation may need a reboot or manual completion. Check wsl --status.'
            }
        }
        Write-Host 'Reboot if prompted; launch Ubuntu once to create a Linux user, then start Ollama and Docker Desktop.'
        if ($HpIp -and $DellIp) {
            & (Join-Path (Split-Path -Parent $PSScriptRoot) 'client\Install-Control-Center.ps1') -Role desktop -HpIp $HpIp -DellIp $DellIp
        } else {
            Write-Host 'Next run client\Install-Control-Center.ps1 -Role desktop -HpIp HP_IP -DellIp DELL_IP to create the desktop app shortcut.'
        }
    }
    'PullModel' {
        $vram = Get-NvidiaVramMiB
        $model = if ($vram -ge 3900) { 'qwen3:4b' } else { 'qwen3:1.7b' }
        $effective = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
        if ([string]::IsNullOrWhiteSpace($effective)) { $effective = $ModelDirectory }
        $effective = Assert-LocalModelPath $effective
        $ollama = Get-OllamaExe
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null }
        catch { throw 'Start the Ollama application from Start, then rerun PullModel.' }
        Write-Host "Pulling $model to $effective for detected $vram MiB VRAM."
        & $ollama pull $model
        if ($LASTEXITCODE -ne 0) { throw "Ollama pull failed for $model." }
        $modelFile = [IO.Path]::GetTempFileName()
        try {
            [IO.File]::WriteAllText($modelFile, "FROM $model`nPARAMETER num_ctx 2048`n", [Text.UTF8Encoding]::new($false))
            & $ollama create homelab-desktop -f $modelFile
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the 2048-token desktop model profile.' }
        } finally {
            Remove-Item -LiteralPath $modelFile -Force -ErrorAction SilentlyContinue
        }
        Write-Host 'Try: ollama run homelab-desktop (2048-token context for limited VRAM).'
        Write-Host 'During a prompt run ollama ps to check GPU loading; lower context or use qwen3:1.7b if it spills to CPU.'
    }
    'Verify' {
        Write-Host "Detected NVIDIA VRAM: $(Get-NvidiaVramMiB) MiB"
        Get-NetAdapter | Select-Object Name, Status, LinkSpeed | Format-Table | Out-Host
        $ollama = Get-OllamaExe
        & $ollama list
        if (Get-Command docker.exe -ErrorAction SilentlyContinue) {
            & docker.exe version --format '{{.Server.Version}}'
        }
        Write-Host "User OLLAMA_MODELS: $([Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User'))"
    }
}
