param(
    [Parameter(Mandatory = $true)][string]$Username,
    [Parameter(Mandatory = $true)][string]$Password
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$env:BOOTSTRAP_ADMIN_USERNAME = $Username
$env:BOOTSTRAP_ADMIN_PASSWORD = $Password
try {
    & ".venv\Scripts\python.exe" scripts\seed_admin.py
}
finally {
    Remove-Item Env:BOOTSTRAP_ADMIN_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:BOOTSTRAP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
