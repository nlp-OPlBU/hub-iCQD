#!/usr/bin/env bash
# Kafka KRaft 存储格式化：只做一次，元数据已存在则跳过
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KAFKA_BIN="/workspace/software/kafka_2.13-4.4.0/bin"
CONF="$ROOT/deploy/kafka/server.properties"
DATA_DIR="/workspace/week14/data/kafka"

mkdir -p "$DATA_DIR"

if [ -f "$DATA_DIR/meta.properties" ]; then
  echo "[kafka] 已格式化，跳过"
  exit 0
fi

CLUSTER_ID="$("$KAFKA_BIN/kafka-storage.sh" random-uuid)"
echo "[kafka] 格式化 $DATA_DIR (cluster=$CLUSTER_ID)"
"$KAFKA_BIN/kafka-storage.sh" format -t "$CLUSTER_ID" -c "$CONF"
