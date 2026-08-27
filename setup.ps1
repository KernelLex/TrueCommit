# Cold-start command 1 of 2 (BUILD.md Day 7 acceptance criterion).
# Creates the venv, installs Python deps, installs dashboard deps.
# Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "Creating Python venv..."
python -m venv .venv

Write-Host "Installing Python dependencies..."
& ".venv\Scripts\pip.exe" install -r requirements.txt

Write-Host "Installing dashboard dependencies..."
Push-Location dashboard
npm install
Pop-Location

Write-Host ""
Write-Host "Setup complete. Next: powershell -ExecutionPolicy Bypass -File run.ps1"
Write-Host "(Optional: copy .env.example to .env and add real API keys for the live-Razorpay/real-channel demos - the app runs fully offline without them.)"
