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
$python = Join-Path $Root ".venv\Scripts\python.exe"
$apiOut = Join-Path $Root "data\api.out.log"
$apiErr = Join-Path $Root "data\api.err.log"
Remove-Item -LiteralPath $apiOut, $apiErr -Force -ErrorAction SilentlyContinue
$apiArguments = @(
    "-m", "dotenv", "-f", ".env", "run", "--override", "--",
    $python, "-m", "uvicorn", "oplab_api.main:app", "--host", "127.0.0.1", "--port", "8000"
)
$api = Start-Process -FilePath $python -ArgumentList $apiArguments -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($api.HasExited) {
            $details = if (Test-Path $apiErr) { Get-Content -Raw $apiErr } else { "No API log." }
            throw "API exited during startup. $details"
        }
        try {
            $health = Invoke-RestMethod -TimeoutSec 2 http://127.0.0.1:8000/health
            if ($health.status -eq "ok") { $ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "API did not become healthy. See $apiErr" }
    Write-Host "API healthy. Logs: $apiOut and $apiErr"
    pnpm.cmd --filter @oplab/web dev
}
finally {
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
