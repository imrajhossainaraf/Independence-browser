. "$PSScriptRoot/_common.ps1"
Assert-Uv
# Step 11 demo: fixtures -> documents -> index -> search offline
Invoke-Native uv @('run', '--frozen', 'search-engine', 'demo')
