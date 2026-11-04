. "$PSScriptRoot/_common.ps1"
Assert-Uv
Invoke-Native uv @('sync', '--frozen')
Write-Host 'install: done'
