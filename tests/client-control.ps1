# Function tests only; never open the GUI or invoke model/Steam actions.
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'client\Client.Common.ps1')
Assert-HomelabModelName 'model Q4_K_M.gguf'
foreach($name in @('../model.gguf','folder\model.gguf','C:\model.gguf','bad"name.gguf','model.bin')) {
    $refused=$false
    try { Assert-HomelabModelName $name } catch { $refused=$true }
    if(-not $refused){throw "Unsafe model name accepted: $name"}
}
if((Quote-HomelabArgument 'C:\some folder\script.ps1') -ne '"C:\some folder\script.ps1"'){throw 'Argument quoting failed'}
$refused=$false
try { Quote-HomelabArgument 'bad"argument' } catch { $refused=$true }
if(-not $refused){throw 'Command argument injection accepted'}
Write-Host 'PASS: Windows control app input and argument guards'
