$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectRoot

$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.runtime\python'
$env:UV_CACHE_DIR = Join-Path $ProjectRoot '.uv-cache'

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $Python) {
    & $Python -m uvicorn hguc_watch.app:app --host 127.0.0.1 --port 8765
} else {
    uv run uvicorn hguc_watch.app:app --host 127.0.0.1 --port 8765
}
