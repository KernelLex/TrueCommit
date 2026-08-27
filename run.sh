#!/usr/bin/env bash
# Cold-start command 2 of 2 (BUILD.md Day 7 acceptance criterion).
# Starts the API in the background and the dashboard in the foreground.
# Usage: ./run.sh
set -euo pipefail

export PK_API_PORT="${PK_API_PORT:-8010}"

echo "Starting API on port $PK_API_PORT ..."
./.venv/bin/python -m uvicorn api.main:app --port "$PK_API_PORT" &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

sleep 3

echo "Starting dashboard (Ctrl+C to stop both)..."
(cd dashboard && npm run dev)
