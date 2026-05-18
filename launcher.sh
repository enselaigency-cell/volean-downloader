#!/bin/bash
# VOLEAN Downloader — macOS launcher (no PID files, no Python daemon)
PORT=8001
URL="http://127.0.0.1:$PORT"
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${VOLEAN_PYTHON:-python3}"
ARCH_PREFIX="${VOLEAN_ARCH:-}"

# ── 1. If Chrome already has our window open, just focus it ──────────────────
CHROME_NAME="Google Chrome"
if ! /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version &>/dev/null; then
  CHROME_NAME="Chromium"
fi

FOCUS_RESULT=$(osascript 2>/dev/null <<APPLESCRIPT
tell application "$CHROME_NAME"
  set found to false
  repeat with w in windows
    try
      if URL of active tab of w contains "127.0.0.1:$PORT" then
        set found to true
        set miniaturized of w to false
        set index of w to 1
        activate
        exit repeat
      end if
    end try
  end repeat
  if found then
    return "focused"
  else
    return "not_found"
  end if
end tell
APPLESCRIPT
)

if [ "$FOCUS_RESULT" = "focused" ]; then
  exit 0
fi

# ── 2. Start uvicorn if not already listening ────────────────────────────────
server_up() {
  python3 -c "
import socket, sys
try:
  s = socket.create_connection(('127.0.0.1', $PORT), timeout=0.5)
  s.close(); sys.exit(0)
except: sys.exit(1)
" 2>/dev/null
}

if ! server_up; then
  cd "$DIR"
  $ARCH_PREFIX "$PYTHON" -m uvicorn app:app \
    --host 127.0.0.1 --port $PORT \
    >> "$DIR/server.log" 2>&1 &
  SERVER_PID=$!

  # Wait up to 25s for server to be ready
  for i in $(seq 1 62); do
    sleep 0.4
    if server_up; then break; fi
  done

  if ! server_up; then
    osascript -e 'display alert "VOLEAN Downloader" message "Сервер не запустился. Проверь server.log" as warning'
    exit 1
  fi
fi

# ── 3. Open Chrome in App Mode ───────────────────────────────────────────────
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -f "$CHROME" ]; then
  CHROME="/Applications/Chromium.app/Contents/MacOS/Chromium"
fi

if [ -f "$CHROME" ]; then
  "$CHROME" \
    --app="$URL" \
    --window-size=980,720 \
    --disable-extensions \
    --no-first-run \
    --disable-default-apps \
    2>/dev/null &
else
  open "$URL"
fi
