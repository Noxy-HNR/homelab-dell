#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Link','Load','Attach','Release','Status')][string]$Action,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$Name=''
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Client.Common.ps1')
$config=Read-HomelabClientConfig $ConfigPath
if ($Action -in @('Link','Load')) {
    if ($config.Role -ne 'laptop') { throw 'Network GGUF loading uses the laptop native stack. Use the desktop local Ollama profile on this PC.' }
    Assert-HomelabModelName $Name
}
if ($Action -eq 'Link') {
    & (Join-Path $config.RepoRoot 'laptop\NetworkModels.ps1') -Action Register -Share $config.ModelsShare -Names $Name -ModelsDir $config.ModelsDir
    Write-Host 'Model linked. Return to the control app and choose Load selected model.'
} elseif ($Action -eq 'Load') {
    if (-not (Test-Path -LiteralPath (Join-Path $config.ModelsDir $Name) -PathType Leaf)) { throw 'Link this model first and check that the HP share is available.' }
    Invoke-RestMethod -Uri ($config.SwitchboardUrl + '/registry?refresh=true') -TimeoutSec 30 | Out-Null
    Write-Host 'Requesting the model through Switchboard. It will enforce its recording, memory, and busy guards.'
    $body=@{model=$Name;reason='Requested in Homelab Control Center'} | ConvertTo-Json
    $result=Invoke-RestMethod -Uri ($config.SwitchboardUrl + '/agent/switch') -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 360
    $result | ConvertTo-Json -Depth 8 | Write-Host
} else {
    if (-not (Test-Path -LiteralPath $config.GameConfig -PathType Leaf)) { throw 'Finish the one-time Steam setup first. The local game-client.json is missing.' }
    & (Join-Path $config.RepoRoot 'client\game-drive\GameDrive.ps1') -Action $Action -ConfigPath $config.GameConfig
}
