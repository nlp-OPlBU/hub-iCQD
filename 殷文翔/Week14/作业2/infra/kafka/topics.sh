#!/usr/bin/env bash
# Only creates configured project topics; uses the same settings as API/Worker.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "${PYTHON:-/usr/local/miniconda3/envs/py312/bin/python}" "$PROJECT_ROOT/scripts/init_topics.py" "$@"
