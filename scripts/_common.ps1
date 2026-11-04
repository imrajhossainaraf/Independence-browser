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
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
