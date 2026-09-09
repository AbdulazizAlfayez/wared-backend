#!/usr/bin/env bash
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PIDS_DIR="$DIR/.pids"
LOGS_DIR="$DIR/logs"
VENV="$DIR/.venv/bin/activate"

# ── Colours ──────────────────────────────────────────────────────
GREEN='\033[0;32m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Activate venv ────────────────────────────────────────────────
if [[ ! -f "$VENV" ]]; then
  echo "ERROR: virtualenv not found at $VENV"
  exit 1
fi
source "$VENV"

# ── Prepare dirs ─────────────────────────────────────────────────
mkdir -p "$PIDS_DIR" "$LOGS_DIR"

# ── Clean up stale PIDs from previous runs ───────────────────────
for pidfile in "$PIDS_DIR"/*.pid; do
  [[ -f "$pidfile" ]] || continue
  pid=$(cat "$pidfile")
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done
# Also kill any orphaned celery processes from previous runs
pkill -9 -f "celery -A car_marketplace" 2>/dev/null || true
sleep 1

# ── Trap: stop background services on exit ───────────────────────
cleanup() {
  echo ""
  echo "Shutting down..."
  bash "$DIR/stop.sh"
}
trap cleanup EXIT

# ── Redis ────────────────────────────────────────────────────────
if redis-cli ping > /dev/null 2>&1; then
  echo -e "${GREEN}Redis${RESET}    already running"
else
  redis-server --daemonize yes --logfile "$LOGS_DIR/redis.log"
  sleep 0.5
  if redis-cli ping > /dev/null 2>&1; then
    echo -e "${GREEN}Redis${RESET}    started  ${DIM}($LOGS_DIR/redis.log)${RESET}"
  else
    echo "ERROR: Redis failed to start"
    exit 1
  fi
fi

# ── Celery worker ────────────────────────────────────────────────
celery -A car_marketplace worker \
  --loglevel=info \
  --logfile="$LOGS_DIR/celery-worker.log" \
  --pidfile="$PIDS_DIR/celery-worker.pid" \
  --detach
echo -e "${GREEN}Celery worker${RESET}  started  ${DIM}($LOGS_DIR/celery-worker.log)${RESET}"

# ── Celery beat ──────────────────────────────────────────────────
celery -A car_marketplace beat \
  --loglevel=info \
  --logfile="$LOGS_DIR/celery-beat.log" \
  --pidfile="$PIDS_DIR/celery-beat.pid" \
  --detach
echo -e "${GREEN}Celery beat${RESET}    started  ${DIM}($LOGS_DIR/celery-beat.log)${RESET}"

# ── Banner ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${RESET}"
echo -e "${BOLD}  MARKABAH Backend${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════${RESET}"
echo -e "  Redis          localhost:6379"
echo -e "  Celery worker  ${DIM}logs/celery-worker.log${RESET}"
echo -e "  Celery beat    ${DIM}logs/celery-beat.log${RESET}"
echo -e "  Django         http://localhost:8000"
echo -e "${BOLD}═══════════════════════════════════════════${RESET}"
echo -e "  ${DIM}Ctrl+C to stop all services${RESET}"
echo ""

# ── Django (foreground) ──────────────────────────────────────────
python manage.py runserver
