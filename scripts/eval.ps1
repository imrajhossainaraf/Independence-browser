. "$PSScriptRoot/_common.ps1"
Assert-Uv
# Step 12 eval: run evaluation harness offline against baseline
$evalArgs = if ($args.Length -gt 0) { $args } else { @('--split', 'dev', '--check-baseline', 'eval/baseline.json') }
Invoke-Native uv (@('run', '--frozen', 'search-engine', 'eval') + $evalArgs)
