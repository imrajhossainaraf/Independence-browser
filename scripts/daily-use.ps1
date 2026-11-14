. "$PSScriptRoot/_common.ps1"
Assert-Uv
Invoke-Native uv (@('run', '--frozen', 'search-engine', 'daily-use') + $args)
