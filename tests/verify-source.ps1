# Offline source checks. Nothing is installed, mounted, formatted, or configured.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked([string]$Label, [string]$Exe, [string[]]$Arguments) {
    Write-Host "Checking $Label"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

$bash = 'C:\Program Files\Git\bin\bash.exe'
if (-not (Test-Path -LiteralPath $bash -PathType Leaf)) { throw 'Git Bash is required for Linux shell syntax checks.' }
$python = (Get-Command python.exe -ErrorAction Stop).Source

Get-ChildItem -LiteralPath $root -Filter '*.sh' -File -Recurse | ForEach-Object {
    Invoke-Checked $_.FullName $bash @('-n', $_.FullName)
}
Get-ChildItem -LiteralPath $root -Filter '*.ps1' -File -Recurse | ForEach-Object {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed: $($_.FullName): $($errors[0])" }
}
Get-ChildItem -LiteralPath $root -Filter '*.py' -File -Recurse | ForEach-Object {
    Invoke-Checked $_.FullName $python @('-m', 'py_compile', $_.FullName)
}
Invoke-Checked 'LIO offline tests' $python @((Join-Path $root 'hp\test_lio_check.py'))
Invoke-Checked 'Steam handoff offline tests' $python @((Join-Path $root 'hp\iscsi\test_game_lun_controller.py'))
Invoke-Checked 'Handoff SSH identity test' $bash @((Join-Path $root 'tests\test_install_controller_identities.sh'))
Invoke-Checked 'Docs and examples' $python @((Join-Path $root 'tests\verify_docs.py'))
Invoke-Checked 'Mocked Windows client guards' 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'tests\windows-offline.ps1'))
Write-Host 'PASS: offline source checks'
