#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
LOCK_DIR="$ROOT_DIR/.locks"
LOG_FILE="$LOG_DIR/vote_status_$(date +%Y%m%d).log"
DEBUG_LOG="${VOTE_STATUS_DEBUG_LOG:-}"
LOCK_FILE="$LOCK_DIR/vote_status.lock"
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

write_log() {
  local line="$1"

  printf '%s\n' "$line" >> "$LOG_FILE"

  if [ -n "$DEBUG_LOG" ]; then
    mkdir -p "$(dirname "$DEBUG_LOG")"
    printf '%s\n' "$line" >> "$DEBUG_LOG"
  fi
}

run_vote_status() {
  local output
  local exit_code
  local summary
  local last_line

  set +e
  output="$("$PYTHON_BIN" src/manage.py save-toto-vote "$@" 2>&1)"
  exit_code=$?
  set -e

  summary="$(printf '%s\n' "$output" | grep -E '^\[(OK|SKIP|ERROR)\]' | tail -n 1 || true)"
  if [ -z "$summary" ]; then
    last_line="$(printf '%s\n' "$output" | awk 'NF { line = $0 } END { print line }')"
    summary="[ERROR] save-toto-vote failed"
    if [ -n "$last_line" ]; then
      summary="$summary | $last_line"
    fi
  fi

  write_log "$(date '+%Y-%m-%d %H:%M:%S') $summary"
  return "$exit_code"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if flock -n 9; then
    run_vote_status "$@"
  else
    write_log "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] vote status job is already running"
  fi
else
  run_vote_status "$@"
fi
