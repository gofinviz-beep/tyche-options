#!/usr/bin/env bash
set -euo pipefail

PORT="${FRONTEND_PORT:-5173}"
PIDS=$(lsof -ti ":$PORT" 2>/dev/null || true)

if [ -z "$PIDS" ]; then
  echo "No process found on port $PORT"
  exit 0
fi

echo "Stopping frontend (port $PORT, PIDs: $(echo "$PIDS" | tr '\n' ' '))..."
kill -9 $PIDS 2>/dev/null || true
sleep 1

if lsof -ti ":$PORT" >/dev/null 2>&1; then
  echo "Warning: process still running on port $PORT"
  exit 1
fi

echo "Frontend stopped."
