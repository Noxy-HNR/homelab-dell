# Links selected GGUF files on the HP share into the existing native model folder.
# A link uses negligible local disk space. No model/config/service is replaced.
param(
    [ValidateSet('Plan', 'Register', 'Verify', 'Unregister')]
    [string]$Action = 'Plan',
    [Parameter(Mandatory = $true)]
    [string]$Share,
    [string[]]$Names = @(),
    [string]$ModelsDir = 'C:\AI\models',
    [string]$ModelsConfig = 'C:\AI\Config\models.json'
)

$ErrorActionPreference = 'Stop'
if ($Share -notmatch '^\\\\[^\\]+\\[^\\]+$') {
    throw 'Share must be a direct UNC share such as \\192.168.1.10\Models, with no trailing slash.'
}
if (-not (Test-Path -LiteralPath $ModelsDir -PathType Container)) {
    throw "Model folder does not exist: $ModelsDir"
}

function Get-LocalEntry([string]$Name) {
    # Directory enumeration also finds a dangling symbolic link when its NAS is offline.
    @(Get-ChildItem -LiteralPath $ModelsDir -Force | Where-Object { $_.Name -ieq $Name }) |
        Select-Object -First 1
}

function Get-Target([System.IO.FileSystemInfo]$Item) {
    if ($null -eq $Item -or $Item -isnot [System.IO.FileInfo] -or $Item.LinkType -ne 'SymbolicLink') { return $null }
    return [string](@($Item.Target)[0])
}

function Test-Name([string]$Name) {
    if ($Name -notmatch '^[^\\/:*?"<>|]+\.gguf$' -or $Name -eq '.' -or $Name -eq '..') {
        throw "Only a top-level .gguf filename is allowed: $Name"
    }
}

function Get-ActiveModel {
    if (-not (Test-Path -LiteralPath $ModelsConfig -PathType Leaf)) {
        throw "Model configuration is unavailable: $ModelsConfig; refusing to remove any links."
    }
    try {
        $active = [string]((Get-Content -LiteralPath $ModelsConfig -Raw | ConvertFrom-Json).activeModel)
        if ([string]::IsNullOrWhiteSpace($active)) { throw 'activeModel is empty.' }
        return $active
    }
    catch { throw "Could not read active model in $ModelsConfig; refusing to remove any links." }
}

Write-Host "Local model folder: $ModelsDir"
Write-Host "HP model share: $Share"
Write-Host 'Local-to-remote link policy:'
& fsutil.exe behavior query symlinkevaluation 2>&1 | ForEach-Object { Write-Host $_ }

if ($Action -eq 'Plan') {
    if (-not (Test-Path -LiteralPath $Share -PathType Container)) {
        throw "Cannot open $Share. Connect to it under the Windows account that runs your model service."
    }
    Get-ChildItem -LiteralPath $Share -Filter '*.gguf' -File |
        Sort-Object Name | Select-Object Name, Length, @{Name='LocalEntry'; Expression={
            $entry = Get-LocalEntry $_.Name
            if ($null -eq $entry) { 'free' }
            elseif ($entry.LinkType -eq 'SymbolicLink') { 'link' }
            else { 'local file - protected' }
        }}
    return
}

if ($Names.Count -eq 0) { throw 'Provide one or more exact GGUF filenames with -Names.' }
foreach ($name in $Names) { Test-Name $name }
$active = if ($Action -eq 'Unregister') { Get-ActiveModel } else { '' }

foreach ($name in $Names) {
    $source = Join-Path $Share $name
    $dest = Join-Path $ModelsDir $name
    $item = Get-LocalEntry $name
    $target = Get-Target $item

    if ($Action -eq 'Register') {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Model is unavailable or incomplete: $source"
        }
        if ($null -ne $item) {
            if ($target -ieq $source) { Write-Host "Already linked: $name"; continue }
            throw "Local name already exists and will not be replaced: $dest"
        }
        New-Item -ItemType SymbolicLink -Path $dest -Target $source -ErrorAction Stop | Out-Null
        if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) {
            throw "Link was created but cannot be read. Check L2R policy and share credentials: $dest"
        }
        Write-Host "Linked without copying: $dest -> $source"
    }
    elseif ($Action -eq 'Verify') {
        if ($target -ine $source) { throw "Expected exact model link is missing: $dest" }
        if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) {
            throw "Link exists but target is unavailable: $dest"
        }
        $file = Get-Item -LiteralPath $dest
        Write-Host ("Available: {0} ({1:N2} GiB)" -f $name, ($file.Length / 1GB))
    }
    elseif ($Action -eq 'Unregister') {
        if ($target -ine $source) { throw "Refusing to remove a local file or unrelated link: $dest" }
        if ($active -ieq $name) { throw "Switch to the local fallback model before unlinking active model: $name" }
        Remove-Item -LiteralPath $dest -Force -ErrorAction Stop
        Write-Host "Removed local link only: $dest"
    }
}
