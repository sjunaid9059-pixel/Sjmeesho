#!/bin/bash
# Start the SJ Meesho web app (FastAPI)
cd "$(dirname "$0")"
export WEBAPP_URL="${WEBAPP_URL:-}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
echo "Starting SJ Shop on http://$HOST:$PORT"
exec python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --workers 1
