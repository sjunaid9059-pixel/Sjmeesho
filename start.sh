#!/bin/bash
# Start the SJ Meesho web app and Telegram bot on Render.
cd "$(dirname "$0")"
export WEBAPP_URL="${WEBAPP_URL:-}"
export SHOP_URL="${SHOP_URL:-https://sj-shop.onrender.com}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
if [ -f bot.py ]; then
  (while true; do python3 bot.py; echo "bot_restart_5s"; sleep 5; done) >> bot.log 2>&1 &
  echo "Telegram bot supervisor started"
fi
echo "Starting SJ Shop on http://$HOST:$PORT"
exec python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --workers 1
