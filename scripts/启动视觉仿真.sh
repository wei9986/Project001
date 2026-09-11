#!/usr/bin/env bash
# 视觉仿真启动脚本（POSIX / 树莓派）
# Windows 开发机请设置：PYTHON_BIN="..\..\.venv\Scripts\python.exe" 或使用上级 .venv\Scripts\python.exe
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

cd "${ROOT_DIR}"
"${PYTHON_BIN}" -m tools.启动检查 --json
exec "${PYTHON_BIN}" -m app.视觉应用 --mode simulation \
  --task "${ROOT_DIR}/config/仿真任务.json" \
  --log "${ROOT_DIR}/logs/application_simulation.jsonl"
