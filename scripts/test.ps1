param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArgs)
. "$PSScriptRoot/_common.ps1"
Assert-Uv
Invoke-Native uv (@('run', '--frozen', 'pytest') + $PytestArgs)
