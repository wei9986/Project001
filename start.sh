#!/usr/bin/env bash
# Portable Raspberry Pi launcher. Default: camera-only development.
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$ROOT_DIR"

mode=vision
source_value="${VISION_SOURCE:-0}"
while (($#)); do
    case "$1" in
        --mode|--source)
            if (($# < 2)); then echo "Missing value for $1" >&2; exit 2; fi
            if [[ "$1" == --mode ]]; then mode="$2"; else source_value="$2"; fi
            shift 2 ;;
        -h|--help)
            echo 'Usage: bash start.sh [--mode vision|simulation|live] [--source 0]'
            echo 'Default: vision (no STM32). --source applies only to vision.'
            echo 'Live camera settings come from config/实机配置.json.'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$mode" in vision|simulation|live) ;; *) echo "Unknown mode: $mode" >&2; exit 2 ;; esac

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python environment not found: $PYTHON_BIN" >&2
    echo 'In the project folder, create .venv and install requirements-pi.txt first.' >&2
    exit 1
fi
# Hold one launcher per project; release automatically when the process exits.
if ! command -v flock >/dev/null 2>&1; then
    echo 'Missing flock. Install util-linux on Raspberry Pi.' >&2
    exit 1
fi
exec 9>"$ROOT_DIR/.vision-launch.lock"
if ! flock -n 9; then
    echo 'A project launcher is already running. Close it before starting again.' >&2
    exit 1
fi
export PYTHONUNBUFFERED=1
echo "Project: $ROOT_DIR"
echo "Mode: $mode"
case "$mode" in
    vision)
        if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
            echo 'Open a terminal on the Raspberry Pi desktop to show the camera window.' >&2
            exit 1
        fi
        echo "Camera/source: $source_value | Q: quit | SPACE: confirm"
        exec "$PYTHON_BIN" -m tools.task_color_test --source "$source_value" ;;
    simulation)
        "$PYTHON_BIN" -m tools.启动检查 --profile simulation
        exec "$PYTHON_BIN" -m app.视觉应用 --mode simulation ;;
    live)
        echo 'LIVE: uses real controller and hardware configuration.'
        "$PYTHON_BIN" -m tools.启动检查 --profile live --require-hardware
        exec "$PYTHON_BIN" -m app.视觉应用 --mode live \
            --live-config "$ROOT_DIR/config/实机配置.json" \
            --test-root "$ROOT_DIR/test_logs" --session-name live ;;
esac
