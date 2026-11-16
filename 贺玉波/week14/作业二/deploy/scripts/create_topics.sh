#!/usr/bin/env bash
# 幂等创建 doc.ingest / doc.ingest.dlq
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
set -a
# shellcheck disable=SC1090
source "$ROOT/.env"
set +a

BOOTSTRAP="${KAFKA__BOOTSTRAP_SERVERS:-127.0.0.1:9092}"
TOPIC="${KAFKA__TOPIC:-doc.ingest}"
DLQ="${KAFKA__DLQ_TOPIC:-doc.ingest.dlq}"
PARTITIONS="${KAFKA__PARTITIONS:-1}"
RETENTION_MS="${KAFKA__RETENTION_MS:-259200000}"
DLQ_RETENTION_MS="${KAFKA__DLQ_RETENTION_MS:-604800000}"
KT="/workspace/software/kafka_2.13-4.4.0/bin/kafka-topics.sh"

"$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic "$TOPIC" --partitions "$PARTITIONS" --replication-factor 1 \
  --config "retention.ms=$RETENTION_MS"

"$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic "$DLQ" --partitions 1 --replication-factor 1 \
  --config "retention.ms=$DLQ_RETENTION_MS"

echo "[topics] 当前 topic："
"$KT" --bootstrap-server "$BOOTSTRAP" --list | sort
