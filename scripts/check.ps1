. "$PSScriptRoot/_common.ps1"
Assert-Uv
Invoke-Native uv @('run', '--frozen', 'python', 'scripts/run_check.py')
