#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

cd "${ROOT_DIR}"
"${PYTHON_BIN}" -m tools.启动检查 --require-hardware --json
exec "${PYTHON_BIN}" -m app.视觉应用 --mode live \
  --live-config "${ROOT_DIR}/config/实机配置.json" \
  --test-root "${ROOT_DIR}/test_logs" \
  --session-name "live"
