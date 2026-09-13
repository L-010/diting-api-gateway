param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if (-not (Test-Path ".env")) {
    $secrets = & ".venv\Scripts\python.exe" -c "import base64,secrets; from cryptography.fernet import Fernet; print(secrets.token_urlsafe(48)); print(Fernet.generate_key().decode()); print(secrets.token_urlsafe(40))"
    $template = Get-Content ".env.example" -Raw
    $template = $template.Replace("replace-with-a-random-32-byte-secret", $secrets[0])
    $template = $template.Replace("replace-with-a-fernet-key", $secrets[1])
    $template = $template.Replace("replace-with-a-random-pepper", $secrets[2])
    Set-Content -Path ".env" -Value $template -Encoding utf8
}
& ".venv\Scripts\python.exe" scripts\migrate.py
if (-not (Test-Path "frontend\node_modules")) {
    Push-Location frontend
    npm ci
    Pop-Location
}
Write-Host "Bootstrap completed. Set TOMODD_UPSTREAM_TOKEN in .env, then run seed-admin.ps1."
