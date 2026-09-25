# Offline source checks. Nothing is installed, mounted, formatted, or configured.
[CmdletBinding()]
param([switch]$Applications)
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
$sources = @(& git -C $root ls-files --cached --others --exclude-standard | ForEach-Object {
    $path = Join-Path $root $_
    if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path }
})
if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate source files from Git.' }

$sources | Where-Object Extension -eq '.sh' | ForEach-Object {
    Invoke-Checked $_.FullName $bash @('-n', $_.FullName)
}
$sources | Where-Object Extension -eq '.ps1' | ForEach-Object {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed: $($_.FullName): $($errors[0])" }
}
$sources | Where-Object Extension -eq '.py' | ForEach-Object {
    Invoke-Checked $_.FullName $python @('-m', 'py_compile', $_.FullName)
}
Invoke-Checked 'LIO offline tests' $python @((Join-Path $root 'hp\test_lio_check.py'))
Invoke-Checked 'Steam handoff offline tests' $python @((Join-Path $root 'hp\iscsi\test_game_lun_controller.py'))
Invoke-Checked 'Handoff SSH identity test' $bash @((Join-Path $root 'tests\test_install_controller_identities.sh'))
Invoke-Checked 'Docs and examples' $python @((Join-Path $root 'tests\verify_docs.py'))
Invoke-Checked 'Mocked Windows client guards' 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'tests\windows-offline.ps1'))
Invoke-Checked 'Windows control app guards' 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'tests\client-control.ps1'))
if ($Applications) {
    $appPython=Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $appPython)) { throw 'Create the test .venv and install services/requirements.in plus pytest first.' }
    $previousPythonPath=$env:PYTHONPATH
    try {
        $env:PYTHONPATH=Join-Path $root 'services'
        Invoke-Checked 'Application workflows' $appPython @('-m','pytest',(Join-Path $root 'services\tests'),'-q')
    } finally { $env:PYTHONPATH=$previousPythonPath }
}
Write-Host 'PASS: offline source checks'
