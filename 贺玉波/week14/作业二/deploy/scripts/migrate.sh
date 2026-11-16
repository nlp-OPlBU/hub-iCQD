#!/usr/bin/env bash
# 幂等建库 + 按序执行 sql/*.sql，用 schema_version 记录已应用脚本
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ ! -f "$ROOT/.env" ]; then
  echo "[migrate] 缺少 $ROOT/.env" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ROOT/.env"
set +a

HOST="${MYSQL__HOST:-127.0.0.1}"
PORT="${MYSQL__PORT:-3306}"
USER="${MYSQL__USER:-root}"
PASS="${MYSQL__PASSWORD:?缺少 MYSQL__PASSWORD}"
DB="${MYSQL__DATABASE:-rag}"

export MYSQL_PWD="$PASS"
MYSQL=(mysql -h"$HOST" -P"$PORT" -u"$USER" --default-character-set=utf8mb4)

echo "[migrate] 建库 $DB"
"${MYSQL[@]}" -e "CREATE DATABASE IF NOT EXISTS \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
"${MYSQL[@]}" "$DB" -e "CREATE TABLE IF NOT EXISTS schema_version (
  version VARCHAR(64) NOT NULL PRIMARY KEY,
  applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"

for file in "$ROOT"/backend/sql/*.sql; do
  name="$(basename "$file")"
  applied="$("${MYSQL[@]}" "$DB" -N -B -e "SELECT COUNT(*) FROM schema_version WHERE version='$name'")"
  if [ "$applied" != "0" ]; then
    echo "[migrate] 跳过 $name"
    continue
  fi
  echo "[migrate] 应用 $name"
  "${MYSQL[@]}" "$DB" < "$file"
  "${MYSQL[@]}" "$DB" -e "INSERT INTO schema_version(version) VALUES ('$name')"
done

echo "[migrate] 完成"
