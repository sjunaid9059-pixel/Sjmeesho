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
if [ -f apply_live_patch.py ] && ! grep -q 'SESSION_COOKIE = "sj_session"' app.py; then
  echo "Applying live auth/payment/admin patch"
  python3 - <<'PY'
from pathlib import Path
import re
p = Path("apply_live_patch.py")
s = p.read_text(encoding="utf-8")
s = re.sub(r"\\+", r"\\", s)
p.write_text(s, encoding="utf-8")
PY
  python3 -m py_compile apply_live_patch.py && python3 apply_live_patch.py || echo "live patch runner failed; starting app"
fi
echo "Starting SJ Shop on http://$HOST:$PORT"
exec python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --workers 1
# Live patch workflow trigger
