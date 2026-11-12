#!/usr/bin/env bash
# 安装 / 校验项目依赖。中间件与模型已由 P0 准备在 /workspace/software、/workspace/models。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="/usr/local/miniconda3/envs/py312/bin/python"
PIP="/usr/local/miniconda3/envs/py312/bin/pip"

if [ ! -f "$ROOT/.env" ]; then
  echo "[bootstrap] 缺少 $ROOT/.env：请 cp .env.example .env 并填写 MYSQL__PASSWORD" >&2
  exit 1
fi

echo "[bootstrap] 安装 Python 依赖"
"$PIP" install -q -r "$ROOT/backend/requirements.txt" -r "$ROOT/backend/requirements-dev.txt"

echo "[bootstrap] 校验配置可加载并创建运行期目录"
PYTHONPATH="$ROOT/backend" "$PY" - <<'PY'
from app.core.config import get_settings

settings = get_settings()
settings.ensure_dirs()
print(f"[bootstrap] ok env={settings.app_env} db={settings.mysql.database} topic={settings.kafka.topic}")
PY

echo "[bootstrap] 完成。下一步：make up"
