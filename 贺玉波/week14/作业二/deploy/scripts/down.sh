#!/usr/bin/env bash
# 停止全部受管进程（含 supervisord 自身）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="$ROOT/deploy/supervisord.conf"
CTL="/usr/local/miniconda3/envs/py312/bin/supervisorctl"

if "$CTL" -c "$CONF" pid >/dev/null 2>&1; then
  "$CTL" -c "$CONF" shutdown
  echo "[down] 已停止"
else
  echo "[down] supervisord 未运行"
fi
