Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\bootstrap.ps1 first." }
$env:PYTHONPATH = Join-Path $root "backend"
& (Join-Path $root ".venv\Scripts\python.exe") -m app.file_worker
