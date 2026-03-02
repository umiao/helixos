# start.ps1 -- Build frontend and launch uvicorn (Windows)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/start.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "frontend"

Write-Host "[BUILD] Building frontend..." -ForegroundColor Cyan
Push-Location $FrontendDir
try {
    npm install
    npm run build
} finally {
    Pop-Location
}

Write-Host "[START] Launching uvicorn..." -ForegroundColor Cyan
Set-Location $ProjectRoot
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload
