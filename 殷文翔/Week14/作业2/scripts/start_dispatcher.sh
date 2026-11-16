#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT/backend"
exec "${PYTHON:-/usr/local/miniconda3/envs/py312/bin/python}" -m app.workers.outbox_dispatcher
