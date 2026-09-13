Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\bootstrap.ps1 first." }
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $projectRoot "backend"
& $pythonPath -m app.email_worker --interval 5
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
