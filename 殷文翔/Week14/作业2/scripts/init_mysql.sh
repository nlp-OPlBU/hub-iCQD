#!/usr/bin/env bash
# Only migrate the configured project database. Never create users/shared services.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT/backend"
exec "${PYTHON:-$PROJECT_ROOT/.venv/bin/python}" -m alembic upgrade head
