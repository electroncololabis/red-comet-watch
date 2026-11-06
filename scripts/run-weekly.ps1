$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectRoot

$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.runtime\python'
$env:UV_CACHE_DIR = Join-Path $ProjectRoot '.uv-cache'

# Exit codes from hguc_watch.runner (see src\hguc_watch\runner.py):
#   0 = success, 1 = failed (no data at all), 2 = partial
# The scheduled task's "Last Result" shows this value, so a failed scan is
# finally visible instead of reporting 0 on every run.
# Log lines below are deliberately ASCII-only: Windows PowerShell reads
# BOM-less .ps1 files as ANSI, and this machine's ANSI codepage is GBK --
# non-ASCII text here would be silently mangled.
$LogPath = Join-Path $ProjectRoot 'data\scan-history.log'
$LogDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

uv run python -m hguc_watch.runner
$Code = $LASTEXITCODE

$Stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$Label = switch ($Code) {
    0 { 'success' }
    1 { 'FAILED' }
    2 { 'partial' }
    default { "exit-$Code" }
}
Add-Content -LiteralPath $LogPath -Value "$Stamp`t$Label`texit=$Code" -Encoding UTF8

if ($Code -ne 0) {
    # Write-Warning, not Write-Error: with $ErrorActionPreference='Stop' the
    # latter is terminating and would replace $Code with 1.
    Write-Warning "HGUC scan did not succeed: $Label (exit $Code). See $LogPath and the app's run history."
}

exit $Code
