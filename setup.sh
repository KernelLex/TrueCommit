#!/usr/bin/env bash
# Cold-start command 1 of 2 (BUILD.md Day 7 acceptance criterion).
# Creates the venv, installs Python deps, installs dashboard deps.
# Usage: ./setup.sh
set -euo pipefail

echo "Creating Python venv..."
python3 -m venv .venv

echo "Installing Python dependencies..."
./.venv/bin/pip install -r requirements.txt

echo "Installing dashboard dependencies..."
(cd dashboard && npm install)

echo
echo "Setup complete. Next: ./run.sh"
echo "(Optional: copy .env.example to .env and add real API keys for the live-Razorpay/real-channel demos - the app runs fully offline without them.)"
