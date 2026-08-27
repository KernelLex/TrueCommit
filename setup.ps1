# Cold-start command 1 of 2 (BUILD.md Day 7 acceptance criterion).
# Creates the venv, installs Python deps, installs dashboard deps.
# Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"

# $ErrorActionPreference only catches PowerShell-native errors, not a
# non-zero exit code from an external exe (python/pip/npm) - those are
# checked explicitly below so a failed step can't be silently followed
# by "Setup complete."
function Assert-LastExitCode($step) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Setup FAILED at: $step (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host "Creating Python venv..."
python -m venv .venv
Assert-LastExitCode "python -m venv .venv"

Write-Host "Installing Python dependencies..."
& ".venv\Scripts\pip.exe" install -r requirements.txt
Assert-LastExitCode "pip install -r requirements.txt"

Write-Host "Installing dashboard dependencies..."
Push-Location dashboard
npm install
Assert-LastExitCode "npm install (dashboard)"
Pop-Location

Write-Host ""
Write-Host "Setup complete. Next: powershell -ExecutionPolicy Bypass -File run.ps1"
Write-Host "(Optional: copy .env.example to .env and add real API keys for the live-Razorpay/real-channel demos - the app runs fully offline without them.)"
Write-Host "(If pip failed with a Windows path-length error: clone the repo to a short path, e.g. C:\pk, or enable Windows long-path support.)"
