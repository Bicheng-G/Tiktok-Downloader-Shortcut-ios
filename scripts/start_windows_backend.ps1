$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing Python environment: $python"
}

Set-Location -LiteralPath $projectRoot
& $python (Join-Path $PSScriptRoot 'windows_backend.py') run
exit $LASTEXITCODE
