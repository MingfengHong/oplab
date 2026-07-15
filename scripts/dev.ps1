param(
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force data, artifacts, uploads | Out-Null

if (-not $NoInstall) {
    uv sync --dev --cache-dir .uv-cache
    pnpm.cmd install
}

Write-Host "Starting API at http://localhost:8000 and workbench at http://localhost:3000"
$api = Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "oplab_api.main:app", "--reload", "--port", "8000") -PassThru -NoNewWindow
try {
    pnpm.cmd --filter @oplab/web dev
}
finally {
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
