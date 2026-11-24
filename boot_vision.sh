#!/usr/bin/env bash
# Invoked by the logged-in graphical desktop, not by root.
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p -- "$ROOT_DIR/logs"
# One bounded log per desktop startup; retain the preceding run for diagnosis.
if [[ -f "$ROOT_DIR/logs/autostart.log" ]]; then
    mv -f -- "$ROOT_DIR/logs/autostart.log" "$ROOT_DIR/logs/autostart.previous.log"
fi
exec >"$ROOT_DIR/logs/autostart.log" 2>&1
date -Is
echo 'Desktop autostart: waiting 5 seconds for camera initialization.'
sleep 5
exec /bin/bash "$ROOT_DIR/start.sh" --mode vision --source "${1:-0}"
