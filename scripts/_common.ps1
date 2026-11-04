# Shared helpers for the PowerShell scripts. Dot-source: . "$PSScriptRoot/_common.ps1"
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# uv installs to ~/.local/bin; make it visible for this process only.
$uvBin = Join-Path $env:USERPROFILE '.local\bin'
if ((Test-Path $uvBin) -and ($env:Path -notlike "*$uvBin*")) { $env:Path = "$uvBin;$env:Path" }

function Assert-Uv {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error 'uv is not installed. See https://docs.astral.sh/uv/ and rerun scripts/install.ps1.'
    }
}

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    # Native tools (uv) write progress to stderr; Windows PowerShell 5.1 turns that into
    # terminating errors under 'Stop'. The exit code is the source of truth.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { exit $code }
}
