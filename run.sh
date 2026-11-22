#!/usr/bin/env bash
# Start E2 (http://localhost:8700 by default; set E2_PORT to change). Restarts if already running.
cd "$(dirname "$0")" || exit 1
PORT=${E2_PORT:-8700}
HOST=${E2_HOST:-0.0.0.0}   # 0.0.0.0 = reachable from other devices on the LAN; 127.0.0.1 = this Mac only
PIDS=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null)
[ -n "$PIDS" ] && { echo "Stopping running E2 (pid $PIDS)…"; kill $PIDS; sleep 1; }
echo "Starting E2 on http://localhost:$PORT"
E2_HOST=$HOST E2_PORT=$PORT exec uv run e2
