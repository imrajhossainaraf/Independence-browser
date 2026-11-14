<#
.SYNOPSIS
    Polls configured news feeds into the Independent Search Engine database.
.DESCRIPTION
    Runs 'search-engine news poll' with process locking and logging.
    Designed for recurring invocation by Windows Task Scheduler.
#>
[CmdletBinding()]
param (
    [string]$ConfigPath = "config/production.toml",
    [string]$SourcesPath = "data/news_sources.txt",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Set-Location $RepoRoot

$ArgsList = @("news", "poll", "--mode", "real")
if (Test-Path $SourcesPath) {
    $ArgsList += @("--sources", $SourcesPath)
}
if ($Force) {
    $ArgsList += "--force"
}
if (Test-Path $ConfigPath) {
    $ArgsList += @("--config-file", $ConfigPath)
}

$VenvExe = Join-Path $RepoRoot ".venv\Scripts\search-engine.exe"
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting news poll..."

if (Test-Path $VenvExe) {
    & $VenvExe @ArgsList
} elseif (Test-Path $VenvPy) {
    & $VenvPy -m search_engine.cli @ArgsList
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run search-engine @ArgsList
} elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    & poetry run search-engine @ArgsList
} else {
    Write-Error "Neither .venv nor uv found in $RepoRoot"
    exit 1
}

$ExitCode = $LASTEXITCODE
Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] News poll finished with exit code $ExitCode"
exit $ExitCode
