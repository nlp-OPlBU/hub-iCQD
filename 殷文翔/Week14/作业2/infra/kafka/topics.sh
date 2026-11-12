#!/usr/bin/env bash
# Create project topics only. Never deletes or alters unrelated shared topics.
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
PARSE_TOPIC="${KAFKA_PDF_TOPIC:-multimodal-rag.pdf.parse}"
FAILED_TOPIC="${KAFKA_FAILED_TOPIC:-multimodal-rag.pdf.failed}"
PARTITIONS="${KAFKA_TOPIC_PARTITIONS:-3}"
REPLICATION="${KAFKA_TOPIC_REPLICATION_FACTOR:-1}"
KAFKA_TOPICS_BIN="${KAFKA_TOPICS_BIN:-kafka-topics.sh}"

for topic in "$PARSE_TOPIC" "$FAILED_TOPIC"; do
  "$KAFKA_TOPICS_BIN" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
    --topic "$topic" --partitions "$PARTITIONS" \
    --replication-factor "$REPLICATION"
done

"$KAFKA_TOPICS_BIN" --bootstrap-server "$BOOTSTRAP" --describe \
  --topic "$PARSE_TOPIC" --topic "$FAILED_TOPIC"
