#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"

cd "$ROOT_DIR"
mkdir -p "$LOG_DIR"

if [ -f .env.server ]; then
  cp -f .env.server .env
fi

docker compose run --rm python python manage.py crawl "$@" 2>&1 | tee -a "$LOG_DIR/crawl_$(date +%Y%m%d).log"
