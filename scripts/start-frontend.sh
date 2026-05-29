#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

cd "$FRONTEND_DIR"
echo "Frontend dev server: http://127.0.0.1:${FRONTEND_PORT:-5173}  (use 127.0.0.1, not localhost)"
if [ "${VITE_NO_HMR:-}" = "1" ]; then
  echo "HMR disabled (VITE_NO_HMR=1)"
fi
exec npm run dev -- --port "${FRONTEND_PORT:-5173}"
