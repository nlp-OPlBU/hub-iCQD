#!/usr/bin/env bash
# 深度研究助手后端启动脚本。
set -euo pipefail
cd "$(dirname "$0")"

# 加载 .env（端口、API key 等）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

exec uvicorn backend.app:app \
    --host "${BACKEND_HOST:-127.0.0.1}" \
    --port "${BACKEND_PORT:-8000}"
