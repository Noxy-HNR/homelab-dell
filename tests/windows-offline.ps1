# Offline checks for the current Windows scripts. No installer, NIC, or disk command runs.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$paths = @(
    (Join-Path $root 'desktop\install.ps1'),
    (Join-Path $root 'laptop\install.ps1'),
    (Join-Path $root 'laptop\NetworkModels.ps1'),
    (Join-Path $root 'client\game-drive\GameDrive.ps1')
)

function Assert-Throws([scriptblock]$Block, [string]$Label, [string]$Message) {
    $threw = $false
    try { & $Block | Out-Null } catch {
        if ($_.Exception.Message -notlike "*$Message*") {
            throw "Wrong refusal for ${Label}: $($_.Exception.Message)"
        }
        $threw = $true
    }
    if (-not $threw) { throw "Expected refusal: $Label" }
}

function Import-FunctionText([string]$Path, [string[]]$Names) {
    $tokens = $null
    $errors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed: $Path : $($errors[0].Message)" }
    foreach ($name in $Names) {
        $matches = @($tree.FindAll({ param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
        }, $true))
        if ($matches.Count -ne 1) { throw "Expected one function $name in $Path" }
        Invoke-Expression ($matches[0].Extent.Text -replace '^function\s+', 'function script:')
    }
}

foreach ($path in $paths) { Import-FunctionText $path @() }
$game = Join-Path $root 'client\game-drive\GameDrive.ps1'
Import-FunctionText $game @('Assert-Value', 'Assert-IPv4', 'Read-Config', 'Get-TargetSession')
Import-FunctionText (Join-Path $root 'desktop\install.ps1') @('Assert-LocalModelPath')
Import-FunctionText (Join-Path $root 'laptop\NetworkModels.ps1') @('Get-ActiveModel')

function Get-IscsiSession { return $script:fakeSession }
function Get-IscsiConnection { param($IscsiSession); return $script:fakeConnection }

$config = [pscustomobject]@{
    HostName = 'desktop'; TargetIqn = 'iqn.2026-09.local.homelab:steam-games'
    PortalAddress = '192.168.1.10'; PortalPort = 3260
    InitiatorAddress = '192.168.1.20'; MinimumLinkSpeedGbps = 10
    ControllerSshHost = '192.168.1.10'; ControllerSshUser = 'gamehandoff-desktop'
    ControllerScriptPath = '/usr/local/sbin/game-lun-controller.py'
    DriveLetter = 'S'; ExpectedVolumeLabel = 'SteamGames'; ChapUsername = ''
}
$temp = Join-Path ([IO.Path]::GetTempPath()) ('homelab-windows-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $keyPath = Join-Path $temp 'test-key'
    'offline-test-only' | Set-Content -LiteralPath $keyPath -Encoding ASCII
    $config | Add-Member -NotePropertyName ControllerSshIdentityFile -NotePropertyValue $keyPath
    $configPath = Join-Path $temp 'client.json'
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    $loaded = Read-Config $configPath
    if ($loaded.PortalAddress -ne $config.PortalAddress) { throw 'Valid iSCSI config was not read.' }
    $config.PortalAddress = '999.168.1.10'
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    Assert-Throws { Read-Config $configPath } 'invalid portal IPv4' 'PortalAddress must be a concrete IPv4'
    $config.PortalAddress = '192.168.1.10'
    $config.InitiatorAddress = '192.168.1.999'
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    Assert-Throws { Read-Config $configPath } 'invalid initiator IPv4' 'InitiatorAddress must be a concrete IPv4'
    $config.InitiatorAddress = '192.168.1.20'
    Assert-Throws { Assert-LocalModelPath '\\server\models' } 'UNC Ollama path' 'local client disk'
    Assert-Throws { Assert-LocalModelPath 'S:\Models' } 'game LUN Ollama path' 'local client disk'

    $script:fakeSession = [pscustomobject]@{
        TargetNodeAddress = $config.TargetIqn; IsPersistent = $false; IsConnected = $true
    }
    $script:fakeConnection = [pscustomobject]@{
        InitiatorAddress = $config.InitiatorAddress
        TargetAddress = $config.PortalAddress; TargetPortNumber = 3260
    }
    if (-not (Get-TargetSession $config)) { throw 'Matching game session was refused.' }
    $script:fakeConnection.TargetAddress = '192.168.1.99'
    Assert-Throws { Get-TargetSession $config } 'session on wrong target IP' 'configured iSCSI network path'
    $script:fakeConnection.TargetAddress = $config.PortalAddress
    $script:fakeConnection.InitiatorAddress = '192.168.1.99'
    Assert-Throws { Get-TargetSession $config } 'session on wrong source IP' 'configured iSCSI network path'
    $script:fakeConnection.InitiatorAddress = $config.InitiatorAddress
    $script:fakeConnection.TargetPortNumber = 3261
    Assert-Throws { Get-TargetSession $config } 'session on wrong port' 'configured iSCSI network path'
    $script:fakeConnection.TargetPortNumber = 3260
    $script:fakeSession.IsPersistent = $true
    Assert-Throws { Get-TargetSession $config } 'persistent session' 'session is persistent'

    $ModelsConfig = Join-Path $temp 'models.json'
    Assert-Throws { Get-ActiveModel } 'missing model config' 'configuration is unavailable'
    '{}' | Set-Content -LiteralPath $ModelsConfig -Encoding UTF8
    Assert-Throws { Get-ActiveModel } 'missing active model' 'Could not read active model'
    '{"activeModel":"local.gguf"}' | Set-Content -LiteralPath $ModelsConfig -Encoding UTF8
    if ((Get-ActiveModel) -ne 'local.gguf') { throw 'Active model was not read.' }
} finally {
    foreach ($file in @($configPath, $keyPath, (Join-Path $temp 'models.json'))) {
        if (Test-Path -LiteralPath $file) {
            Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}

# Supply an in-process fake executable; no WinGet process or installer is run.
$script:wingetCalls = New-Object 'System.Collections.Generic.List[string]'
$script:wingetListExit = 0
$script:fakeWinget = {
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
    $script:wingetCalls.Add([string]$Arguments[0])
    $global:LASTEXITCODE = if ($Arguments[0] -eq 'list') { $script:wingetListExit } else { 0 }
}
function Get-Command {
    param([string]$Name)
    if ($Name -ne 'winget.exe') { throw "Unexpected command lookup in offline test: $Name" }
    return [pscustomobject]@{ Source = $script:fakeWinget }
}
try {
    foreach ($path in @((Join-Path $root 'desktop\install.ps1'), (Join-Path $root 'laptop\install.ps1'))) {
        Import-FunctionText $path @('Install-ByWinget')
        $script:wingetCalls.Clear()
        $script:wingetListExit = 0
        Install-ByWinget 'Git.Git' | Out-Null
        if (($script:wingetCalls -join ',') -ne 'list') {
            throw "Installed package was not skipped by $path"
        }
        $script:wingetCalls.Clear()
        $script:wingetListExit = 1
        Install-ByWinget 'Git.Git' | Out-Null
        if (($script:wingetCalls -join ',') -ne 'list,install') {
            throw "Absent package was not installed by $path"
        }
    }
} finally {
    Remove-Item function:Get-Command -ErrorAction SilentlyContinue
}
Write-Host 'PASS: four scripts parse; mocked WinGet skip/install paths; iSCSI and model guards.'
