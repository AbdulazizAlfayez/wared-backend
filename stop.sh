#!/usr/bin/env bash
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDS_DIR="$DIR/.pids"

GREEN='\033[0;32m'
RESET='\033[0m'

stopped=0

for pidfile in "$PIDS_DIR"/*.pid; do
  [[ -f "$pidfile" ]] || continue
  name="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    echo -e "${GREEN}Stopped${RESET}  $name (PID $pid)"
    stopped=$((stopped + 1))
  else
    echo "Stale    $name (PID $pid already gone)"
  fi
  rm -f "$pidfile"
done

# Kill any remaining celery child processes (workers fork children)
pkill -9 -f "celery -A car_marketplace" 2>/dev/null || true

# Kill any lingering Django runserver processes
pkill -9 -f "manage.py runserver" 2>/dev/null || true

if [[ $stopped -eq 0 ]]; then
  echo "No background services were running."
else
  echo ""
  echo "$stopped service(s) stopped. Redis left running."
fi
