#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Starting Backend ==="
"$SCRIPT_DIR/start-backend.sh" &
BACKEND_PID=$!

echo "=== Starting Frontend ==="
"$SCRIPT_DIR/start-frontend.sh" &
FRONTEND_PID=$!

trap 'echo "Shutting down..."; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; wait' EXIT INT TERM

echo ""
echo "Backend PID:  $BACKEND_PID  (http://127.0.0.1:${BACKEND_PORT:-8000})"
echo "Frontend PID: $FRONTEND_PID (http://127.0.0.1:${FRONTEND_PORT:-5173})"
echo ""
echo "Open the app at:  http://127.0.0.1:${FRONTEND_PORT:-5173}"
echo "Use 127.0.0.1 — NOT localhost (corporate PAC proxy breaks localhost in browsers)."
echo "Press Ctrl+C to stop both."
echo ""

wait
