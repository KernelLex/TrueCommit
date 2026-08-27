# Cold-start command 2 of 2 (BUILD.md Day 7 acceptance criterion).
# Starts the API in the background and the dashboard in the foreground.
# Usage:  powershell -ExecutionPolicy Bypass -File run.ps1

$ErrorActionPreference = "Stop"
if (-not $env:PK_API_PORT) { $env:PK_API_PORT = "8010" }

Write-Host "Starting API on port $env:PK_API_PORT ..."
$api = Start-Process -PassThru -NoNewWindow -FilePath ".venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "api.main:app", "--port", "$env:PK_API_PORT"

Start-Sleep -Seconds 3

if ($api.HasExited) {
    Write-Host "API process exited immediately (exit code $($api.ExitCode)) - check that setup.ps1 completed successfully." -ForegroundColor Red
    exit 1
}

Write-Host "Starting dashboard (Ctrl+C to stop both)..."
try {
    Push-Location dashboard
    npm run dev
} finally {
    Pop-Location
    Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
}
