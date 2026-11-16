#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$(dirname -- "${PYTHON:-/usr/local/miniconda3/envs/py312/bin/python}"):$PATH"
exec "${PYTHON:-/usr/local/miniconda3/envs/py312/bin/python}" "$PROJECT_ROOT/scripts/run_project.py" "$@"
