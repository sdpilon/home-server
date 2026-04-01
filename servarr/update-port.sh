#!/bin/sh

QB_HOST="${QB_HOST:-http://localhost:8080}"
PORT_FILE="${PORT_FILE:-/tmp/gluetun/forwarded_port}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"

set_port() {
  wget -q -O - \
    --post-data "json=%7B%22listen_port%22%3A$1%7D" \
    "${QB_HOST}/api/v2/app/setPreferences"
}

sleep 10

CURRENT_PORT=""
echo "[port-updater] Watching ${PORT_FILE} every ${POLL_INTERVAL}s..."

while true; do
  sleep "$POLL_INTERVAL"

  [ -f "$PORT_FILE" ] || { echo "[port-updater] Port file not found, waiting..."; continue; }

  NEW_PORT=$(tr -d '[:space:]' < "$PORT_FILE")
  [ -n "$NEW_PORT" ]          || continue
  [ "$NEW_PORT" != "$CURRENT_PORT" ] || continue

  echo "[port-updater] Port changed: ${CURRENT_PORT:-none} -> ${NEW_PORT}"

  # setPreferences returns empty body on success
  SET_RESULT=$(set_port "$NEW_PORT")
  if [ -z "$SET_RESULT" ]; then
    echo "[port-updater] Port updated to ${NEW_PORT}"
    CURRENT_PORT="$NEW_PORT"
  else
    echo "[port-updater] Failed to set port: ${SET_RESULT}"
  fi
done