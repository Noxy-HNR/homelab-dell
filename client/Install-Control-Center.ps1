#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('desktop','laptop')][string]$Role,
    [Parameter(Mandatory=$true)][string]$HpIp,
    [Parameter(Mandatory=$true)][string]$DellIp
)
$ErrorActionPreference='Stop'
foreach ($value in @($HpIp,$DellIp)) {
    $ip=$null
    if (-not [Net.IPAddress]::TryParse($value,[ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $ip.ToString() -ne $value) { throw 'Use real IPv4 addresses for HP and Dell.' }
}
$repo = Split-Path -Parent $PSScriptRoot
$folder = Join-Path $env:LOCALAPPDATA 'Homelab'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$path = Join-Path $folder 'client-settings.json'
if (Test-Path -LiteralPath $path) {
    Copy-Item -LiteralPath $path -Destination ($path + '.before-' + (Get-Date -Format 'yyyyMMddHHmmss'))
}
$config = [ordered]@{
    Role=$Role; HpUrl="http://${HpIp}:8088"; DellUrl="http://${DellIp}:8090"
    ModelsShare="\\$HpIp\Models"; RepoRoot=$repo; ModelsDir='C:\AI\models'
    SwitchboardUrl='http://127.0.0.1:8002'
    GameConfig=(Join-Path $repo 'client\game-drive\game-client.json')
}
$config | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Homelab Control Center.lnk'))
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = '-NoProfile -STA -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'Control-Center.ps1') + '" -ConfigPath "' + $path + '"'
$shortcut.WorkingDirectory=$repo
$shortcut.Description='Models, server workspaces, and the shared Steam drive'
$shortcut.Save()
Write-Host 'Installed the Homelab Control Center desktop shortcut.'
Write-Host "Settings: $path"
Write-Host 'Keep this repository folder in place. The app asks for the HP token when you refresh models; it does not save that token.'
