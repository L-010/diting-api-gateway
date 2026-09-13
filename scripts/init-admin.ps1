param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$Username = "admin"
$Password = "Admin@123456"

$env:BOOTSTRAP_ADMIN_USERNAME = $Username
$env:BOOTSTRAP_ADMIN_PASSWORD = $Password

try {
    & ".venv\Scripts\python.exe" scripts\seed_admin.py
    Write-Host "✓ 管理员账户已创建: $Username"
}
finally {
    Remove-Item Env:BOOTSTRAP_ADMIN_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:BOOTSTRAP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
