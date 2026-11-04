. "$PSScriptRoot/_common.ps1"
Assert-Uv
# Static security checks. pip-audit contacts the package index; run it only when that is allowed.
Invoke-Native uv @('run', '--frozen', 'bandit', '-q', '-r', 'src', '-ll')
Invoke-Native uv @('run', '--frozen', 'pip-licenses', '--format=markdown')
if ($env:AUDIT_ALLOW_INDEX -eq '1') {
    Invoke-Native uv @('run', '--frozen', 'pip-audit')
} else {
    Write-Host 'audit: pip-audit skipped (set AUDIT_ALLOW_INDEX=1 to allow package-index access).'
}
