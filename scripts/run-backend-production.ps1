Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "请先创建项目虚拟环境并安装后端依赖。" }
if ($env:APP_ENV -ne "production") { throw "生产启动脚本要求 APP_ENV=production。" }
$workers = 1
if ($env:AGW_WORKERS) {
    if (-not [int]::TryParse($env:AGW_WORKERS, [ref]$workers) -or $workers -lt 1 -or $workers -gt 8) {
        throw "AGW_WORKERS 必须是 1 到 8 之间的整数。"
    }
}
$hostAddress = "0.0.0.0"
if ($env:AGW_HOST) { $hostAddress = $env:AGW_HOST }
if ($hostAddress -notin @("0.0.0.0", "127.0.0.1", "::1")) {
    throw "AGW_HOST 只能是 0.0.0.0、127.0.0.1 或 ::1。"
}
$port = 8000
if ($env:AGW_PORT) {
    if (-not [int]::TryParse($env:AGW_PORT, [ref]$port) -or $port -lt 1024 -or $port -gt 65535) {
        throw "AGW_PORT 必须是 1024 到 65535 之间的整数。"
    }
}
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host $hostAddress --port $port --workers $workers

