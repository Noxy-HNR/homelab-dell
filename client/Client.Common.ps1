# Shared validation for the Windows control application. Importing performs no actions.
function Read-HomelabClientConfig([string]$Path) {
    $c = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($c.Role -notin @('desktop', 'laptop')) { throw 'Role must be desktop or laptop.' }
    foreach ($key in @('HpUrl', 'DellUrl')) {
        $uri = $null
        if (-not [Uri]::TryCreate([string]$c.$key, [UriKind]::Absolute, [ref]$uri) -or
            $uri.Scheme -notin @('http','https') -or $uri.UserInfo) { throw "Invalid $key in client settings." }
    }
    if ($c.ModelsShare -notmatch '^\\\\[^\\]+\\Models$') { throw 'ModelsShare must name the HP Models share.' }
    if ($c.SwitchboardUrl -ne 'http://127.0.0.1:8002') { throw 'Switchboard must use the existing local loopback endpoint.' }
    if (-not (Test-Path -LiteralPath $c.RepoRoot -PathType Container)) { throw 'The homelab-stack clone has moved. Run Install-Control-Center again.' }
    return $c
}

function Assert-HomelabModelName([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -match '[\\/:*?"<>|\x00-\x1f]' -or $Name -notmatch '(?i)\.gguf$') {
        throw 'Choose a GGUF directly in the Models share. Nested files need a top-level copy or download name.'
    }
}

function Quote-HomelabArgument([string]$Value) {
    if ($Value -match '["\r\n\x00]') { throw 'Unsupported command argument.' }
    return '"' + $Value + '"'
}
