. "$PSScriptRoot/_common.ps1"
Assert-Uv
# Step 1 demo: prove the CLI runs. The fixtures-to-search demo arrives in Step 11.
Invoke-Native uv @('run', '--frozen', 'search-engine', 'health')
