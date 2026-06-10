#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  ENTRY_FILE="${ENTRY_FILE-}" \
  PYTHON_BIN="${PYTHON_BIN-}" \
  LOG_FILE="${LOG_FILE-}" \
  PID_FILE="${PID_FILE-}" \
  bash "${BASH_SOURCE[0]}"
  return $?
fi

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRY_FILE="${ENTRY_FILE:-crypto_price_server.py}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
LOG_FILE="${LOG_FILE:-server.log}"
PID_FILE="${PID_FILE:-price_server.pid}"

cd "$APP_DIR"

echo "Stopping price server..."
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if [[ "$OLD_PID" =~ ^[0-9]+$ ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$OLD_PID" 2>/dev/null; then
        break
      fi
      sleep 0.2
    done
    if kill -0 "$OLD_PID" 2>/dev/null; then
      kill -9 "$OLD_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
fi

pkill -f "$APP_DIR/$ENTRY_FILE" 2>/dev/null || true
pkill -f "$ENTRY_FILE" 2>/dev/null || true

echo "Starting price server..."
nohup "$PYTHON_BIN" -u "$ENTRY_FILE" > "$LOG_FILE" 2>&1 &
NEW_PID="$!"
echo "$NEW_PID" > "$PID_FILE"

sleep 0.5
if kill -0 "$NEW_PID" 2>/dev/null; then
  echo "Price server restarted. PID: $NEW_PID"
  echo "Log file: $APP_DIR/$LOG_FILE"
else
  echo "Price server failed to start. Check log: $APP_DIR/$LOG_FILE"
  exit 1
fi
