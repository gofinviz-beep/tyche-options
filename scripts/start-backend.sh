#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

source "$BACKEND_DIR/.venv/bin/activate"
cd "$BACKEND_DIR"
echo "Backend API: http://127.0.0.1:${BACKEND_PORT:-8000}/health"
exec uvicorn tyche.app:app --reload --host 127.0.0.1 --port "${BACKEND_PORT:-8000}"
