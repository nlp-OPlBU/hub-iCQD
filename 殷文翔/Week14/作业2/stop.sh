#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-/usr/local/miniconda3/envs/py312/bin/python}"
export PATH="$(dirname -- "$PYTHON_BIN"):$PATH"
exec "$PYTHON_BIN" "$PROJECT_ROOT/scripts/stop_project.py" "$@"
