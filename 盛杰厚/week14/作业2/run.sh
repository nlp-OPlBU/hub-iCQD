#!/usr/bin/env bash
# 一键启动：Kafka(KRaft) -> 等 broker 就绪 -> 起 Web（进程内自带 Worker）
#
#   ./run.sh          默认：起 Kafka + Web
#   ./run.sh kafka    只起 Kafka
#   ./run.sh serve    只起 Web（假设 Kafka 已在跑）
#   ./run.sh worker   单独跑 Worker 进程（配合 .env 里 WORKERS_INPROC=0）
#   ./run.sh smoke    端到端冒烟测试（自动起临时 Web，跑完关掉）
#   ./run.sh down     停掉 Kafka（加 -v 会连 topic 数据一起删：./run.sh down -v）
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8010}"
PY="${PYTHON:-python}"
# 没指定 PYTHON 时，优先用 conda 的 py312（课程环境）
if [[ -z "${PYTHON:-}" ]] && command -v conda >/dev/null 2>&1; then
  PY="conda run -n py312 --no-capture-output python"
fi

start_kafka() {
  echo "▶ 启动 Kafka（docker compose，KRaft 单节点）…"
  docker compose up -d
  echo -n "  等待 broker 健康"
  for _ in $(seq 1 40); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' mmrag-kafka 2>/dev/null || true)" == "healthy" ]]; then
      echo " ✓ mmrag-kafka healthy (localhost:9092)"; return 0
    fi
    echo -n "."; sleep 3
  done
  echo " ✗ Kafka 一直没健康，看日志： docker compose logs --tail 60 kafka"; exit 1
}

case "${1:-all}" in
  kafka) start_kafka ;;
  serve)
    echo "▶ 启动 Web： http://localhost:${PORT}  （Ctrl-C 退出）"
    exec $PY -m uvicorn app:app --host 0.0.0.0 --port "$PORT" ;;
  worker)
    shift || true
    echo "▶ 启动 Worker（roles=${1:-all}）"
    exec $PY worker.py --roles "${1:-all}" ;;
  smoke)
    shift || true
    exec $PY scripts/smoke.py "$@" ;;
  down)
    shift || true
    docker compose down "${1:-}" ;;
  all)
    start_kafka
    echo "▶ 启动 Web： http://localhost:${PORT}  （Ctrl-C 退出）"
    exec $PY -m uvicorn app:app --host 0.0.0.0 --port "$PORT" ;;
  *)
    echo "用法： ./run.sh [all|kafka|serve|worker|smoke|down]"; exit 2 ;;
esac
