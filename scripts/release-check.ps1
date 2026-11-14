<#
.SYNOPSIS
    Release verification script for Independent Search Engine v0.1.0.
.DESCRIPTION
    Clones the repository into a clean temporary workspace and executes the end-to-end
    release verification pipeline: clean install, full check (format, lint, mypy, pytest),
    fixture crawl, fixture news poll, demo indexing, search smoke test, daily-use checklist,
    benchmarks, backup, and restore.
#>
[CmdletBinding()]
param(
    [switch]$KeepWorkspace
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_common.ps1"
Assert-Uv

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$TempRoot = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "search-engine-release-" + [System.Guid]::NewGuid().ToString("N"))
Write-Host "==> Creating clean checkout in: $TempRoot" -ForegroundColor Cyan

try {
    # 1. Clean clone from local repository without touching network
    Invoke-Native git @('clone', '--quiet', $RepoRoot, $TempRoot)

    Push-Location $TempRoot
    try {
        Write-Host "==> [Stage 1/9] Installing dependencies in clean checkout..." -ForegroundColor Cyan
        Invoke-Native uv @('sync', '--frozen')

        Write-Host "==> [Stage 2/9] Running project verification check..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'python', 'scripts/run_check.py')

        Write-Host "==> [Stage 3/9] Running fixture crawl..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'crawl', '--mode', 'fixture')

        Write-Host "==> [Stage 4/9] Running fixture news poll..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'news', 'poll', '--mode', 'fixture')

        Write-Host "==> [Stage 5/9] Running vertical slice demo & index rebuild..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'demo')

        Write-Host "==> [Stage 6/9] Running offline search smoke test..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'search', 'python')

        Write-Host "==> [Stage 7/9] Running automated daily-use checklist..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'daily-use')

        Write-Host "==> [Stage 8/9] Running Section 7 synthetic benchmarks..." -ForegroundColor Cyan
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'benchmark')

        Write-Host "==> [Stage 9/9] Running backup and restore verification..." -ForegroundColor Cyan
        $BackupDir = Join-Path $TempRoot "backups"
        $RestoreDir = Join-Path $TempRoot "restored"
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'backup', '--output-dir', $BackupDir)
        
        $LatestBackup = Get-ChildItem -Path $BackupDir -Directory | Sort-Object CreationTime -Descending | Select-Object -First 1
        if (-not $LatestBackup) {
            throw "Backup failed: no snapshot directory found in $BackupDir"
        }
        Invoke-Native uv @('run', '--frozen', 'search-engine', 'restore', '--backup-dir', $LatestBackup.FullName, '--target-dir', $RestoreDir)

        Write-Host "==> Release check v0.1.0: ALL STAGES PASSED SUCCESSFULLY!" -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
}
finally {
    if (-not $KeepWorkspace -and (Test-Path $TempRoot)) {
        Write-Host "==> Cleaning up temporary workspace: $TempRoot"
        # Retry briefly for Windows file lock release
        for ($i = 0; $i -lt 5; $i++) {
            try {
                Remove-Item -Path $TempRoot -Recurse -Force -ErrorAction Stop
                break
            }
            catch {
                Start-Sleep -Milliseconds 500
            }
        }
    }
}
