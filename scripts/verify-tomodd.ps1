Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
& ".venv\Scripts\python.exe" scripts\verify_tomodd.py
exit $LASTEXITCODE
