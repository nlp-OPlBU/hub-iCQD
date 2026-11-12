#!/usr/bin/env bash
# 按依赖顺序启动：中间件 → 建库建 topic → vLLM → api，最后跑 /ready
# 用法：up.sh [all|core]   core = 不启动 vLLM（本地调试用）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-all}"
CONF="$ROOT/deploy/supervisord.conf"
CTL="/usr/local/miniconda3/envs/py312/bin/supervisorctl"
SUPERVISORD="/usr/local/miniconda3/envs/py312/bin/supervisord"

# 云端对话模型时不需要本地 vLLM；.env 没写这个键就按 local 处理
PROVIDER="$(sed -n 's/^VLLM__PROVIDER=//p' "$ROOT/.env" 2>/dev/null | tail -1 | tr -d '\r"')"
PROVIDER="${PROVIDER:-local}"

mkdir -p "$ROOT/logs" "$ROOT/logs/mineru" "$ROOT/run" \
  "$ROOT/data/pdf" "$ROOT/data/parsed" "$ROOT/data/images" "$ROOT/data/milvus" "$ROOT/data/redis" "$ROOT/data/kafka"

bash "$ROOT/deploy/scripts/format_kafka.sh"

if ! "$CTL" -c "$CONF" pid >/dev/null 2>&1; then
  echo "[up] 启动 supervisord"
  "$SUPERVISORD" -c "$CONF"
  sleep 3
fi
"$CTL" -c "$CONF" reread >/dev/null
"$CTL" -c "$CONF" update >/dev/null

wait_port() {
  local port=$1 name=$2 tries=${3:-60}
  for _ in $(seq 1 "$tries"); do
    if (echo >"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
      echo "[up] $name 就绪 (:$port)"
      return 0
    fi
    sleep 2
  done
  echo "[up] $name 启动超时 (:$port)，查看 logs/$name.log" >&2
  return 1
}

ensure_running() {
  local name=$1
  local state
  state="$("$CTL" -c "$CONF" status "$name" 2>/dev/null | awk '{print $2}' || true)"
  case "$state" in
    RUNNING | STARTING) ;;
    *) "$CTL" -c "$CONF" start "$name" || true ;;
  esac
}

ensure_running mysqld
ensure_running redis
ensure_running kafka
wait_port 3306 mysqld
wait_port 6379 redis
wait_port 9092 kafka 90
# 向量库独立进程：api 与 worker 都要连它
ensure_running milvus
wait_port 19530 milvus

# MinerU 4.x 的 parse（含 --tier flash/--remote）都必须先有本地解析服务；
# 它是内部 vLLM + Qwen2VL 的常驻进程，重启实例后需要重新拉起，否则上传一律失败
MINERU="/usr/local/miniconda3/envs/py312/bin/mineru"
if (echo >"/dev/tcp/127.0.0.1/16580") >/dev/null 2>&1; then
  echo "[up] MinerU 解析服务已在运行 (:16580)"
else
  echo "[up] 启动 MinerU 解析服务（内部 vLLM，模型加载约 2 分钟）"
  PATH="/usr/local/cuda/bin:/usr/local/bin:$PATH" "$MINERU" server start >/dev/null 2>&1 || true
  wait_port 16580 mineru-server 180
fi

bash "$ROOT/deploy/scripts/migrate.sh"
bash "$ROOT/deploy/scripts/create_topics.sh"

if [ "$PROVIDER" = "cloud" ]; then
  echo "[up] provider=cloud：跳过本地 vLLM，使用云端对话模型"
elif [ "$MODE" = "all" ]; then
  echo "[up] 启动 vLLM（首次加载约 3 分钟）"
  ensure_running vllm
else
  echo "[up] core 模式：跳过 vLLM"
fi

ensure_running api
ensure_running worker

ready_ok() {
  curl -fsS "http://127.0.0.1:8080/$1" 2>/dev/null | grep -q '"status":"ok"'
}

# core 模式没有 vLLM，/ready 必然 503，只要求 /health 通过；
# 云端 provider 的就绪检查会真的打云端 API，因此仍然等 /ready
if [ "$MODE" = "all" ] || [ "$PROVIDER" = "cloud" ]; then
  PROBE=ready
else
  PROBE=health
fi

echo "[up] 等待 /$PROBE"
for i in $(seq 1 210); do
  if ready_ok "$PROBE"; then
    echo "[up] /$PROBE 通过"
    curl -sS "http://127.0.0.1:8080/ready"; echo
    "$CTL" -c "$CONF" status || true
    exit 0
  fi
  sleep 2
done

echo "[up] /ready 未全绿，当前状态：" >&2
curl -sS "http://127.0.0.1:8080/ready" >&2 || true
echo >&2
"$CTL" -c "$CONF" status >&2 || true
exit 1
