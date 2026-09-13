Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location (Join-Path $root "frontend")
if (-not (Test-Path "node_modules")) { throw "Run scripts\bootstrap.ps1 first." }
npm run dev
