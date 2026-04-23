#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
LOCK_DIR="$ROOT_DIR/.locks"
LOG_FILE="$LOG_DIR/crawling_$(date +%Y%m%d).log"
LOCK_FILE="$LOCK_DIR/crawling.lock"
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

run_crawling() {
  local output
  local exit_code
  local summary
  local last_line
  local crawled_rounds
  local skipped_rounds
  local failed_rounds

  set +e
  output="$("$PYTHON_BIN" src/manage.py crawl "$@" 2>&1)"
  exit_code=$?
  set -e

  if [ "$exit_code" -ne 0 ]; then
    last_line="$(printf '%s\n' "$output" | awk 'NF { line = $0 } END { print line }')"
    summary="[ERROR] crawl failed"
    if [ -n "$last_line" ]; then
      summary="$summary | $last_line"
    fi
  else
    crawled_rounds="$(printf '%s\n' "$output" | grep -c 'round_id=' || true)"
    skipped_rounds="$(printf '%s\n' "$output" | grep -c 'results already saved, skip' || true)"
    failed_rounds="$(printf '%s\n' "$output" | grep -c 'failed to ' || true)"

    if printf '%s\n' "$output" | grep -q 'no schedules found'; then
      summary="[SKIP] no schedules found"
    else
      summary="[OK] crawl completed | rounds=${crawled_rounds} skips=${skipped_rounds} failures=${failed_rounds}"
    fi
  fi

  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$summary" | tee -a "$LOG_FILE"
  return "$exit_code"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if flock -n 9; then
    run_crawling "$@"
  else
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "[SKIP] crawling job is already running" | tee -a "$LOG_FILE"
  fi
else
  run_crawling "$@"
fi
