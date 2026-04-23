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
  local output
  local exit_code
  local summary
  local last_line

  set +e
  output="$("$PYTHON_BIN" src/manage.py results "$@" 2>&1)"
  exit_code=$?
  set -e

  summary="$(printf '%s\n' "$output" | grep -E '^\[(SUMMARY|OK|SKIP|ERROR)\]' | tail -n 1 || true)"
  if [ -z "$summary" ]; then
    last_line="$(printf '%s\n' "$output" | awk 'NF { line = $0 } END { print line }')"
    summary="[ERROR] results failed"
    if [ -n "$last_line" ]; then
      summary="$summary | $last_line"
    fi
  fi

  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$summary" | tee -a "$LOG_FILE"
  return "$exit_code"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if flock -n 9; then
    run_results "$@"
  else
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "[SKIP] results job is already running" | tee -a "$LOG_FILE"
  fi
else
  run_results "$@"
fi
