#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
LOCK_DIR="$ROOT_DIR/.locks"
LOG_FILE="$LOG_DIR/results_$(date +%Y%m%d).log"
LOCK_FILE="$LOCK_DIR/results.lock"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

if [ -f .env.server ]; then
  cp -f .env.server .env
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/venv/bin/python"
fi

run_results() {
  "$PYTHON_BIN" src/manage.py results "$@" 2>&1 | tee -a "$LOG_FILE"
}

if command -v flock >/dev/null 2>&1; then
  flock -n "$LOCK_FILE" bash -lc 'cd "'"$ROOT_DIR"'" && "'"$PYTHON_BIN"'" src/manage.py results "$@" 2>&1 | tee -a "'"$LOG_FILE"'"' -- "$@"
else
  run_results "$@"
fi
