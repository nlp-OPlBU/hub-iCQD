from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import ColumnInfo, ForeignKeyInfo, SchemaCatalog, TableInfo


SENSITIVE_COLUMNS = {"email", "phone", "fax", "address"}


def quote_identifier(name: str) -> str:
    """安全引用来自 sqlite_master 的标识符。"""
    return '"' + name.replace('"', '""') + '"'


def readonly_uri(path: Path) -> str:
    """生成 SQLite 只读 URI；写操作从连接层即被禁止。"""
    return f"{path.resolve().as_uri()}?mode=ro"


class SchemaService:
    """解析并缓存供选表和 SQL 生成使用的数据库目录。"""

    def __init__(self, db_path: Path, sample_rows: int = 2) -> None:
        self.db_path = db_path
        self.sample_rows = sample_rows
        self._cache: SchemaCatalog | None = None
        self._cache_mtime_ns: int | None = None

    def load(self) -> SchemaCatalog:
        mtime_ns = self.db_path.stat().st_mtime_ns
        # 数据库未变化时复用目录，避免每个问题重复扫描所有表和样本。
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return self._cache

        tables: dict[str, TableInfo] = {}
        with sqlite3.connect(readonly_uri(self.db_path), uri=True) as connection:
            connection.row_factory = sqlite3.Row
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for name in names:
                quoted = quote_identifier(name)
                columns = [
                    ColumnInfo(
                        name=row["name"],
                        declared_type=row["type"] or "",
                        not_null=bool(row["notnull"]),
                        primary_key=bool(row["pk"]),
                    )
                    for row in connection.execute(f"PRAGMA table_info({quoted})")
                ]
                foreign_keys = [
                    ForeignKeyInfo(
                        from_column=row["from"],
                        target_table=row["table"],
                        target_column=row["to"],
                    )
                    for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
                ]
                row_count = connection.execute(
                    f"SELECT COUNT(*) FROM {quoted}"
                ).fetchone()[0]
                sample_records = connection.execute(
                    f"SELECT * FROM {quoted} LIMIT ?", (self.sample_rows,)
                ).fetchall()
                samples = [
                    self._redact_sample(dict(record)) for record in sample_records
                ]
                tables[name] = TableInfo(
                    name=name,
                    columns=columns,
                    foreign_keys=foreign_keys,
                    row_count=row_count,
                    samples=samples,
                )

        self._cache = SchemaCatalog(tables=tables)
        self._cache_mtime_ns = mtime_ns
        return self._cache

    def selection_context(self, catalog: SchemaCatalog | None = None) -> str:
        """生成轻量目录，选表阶段不发送样本以减少干扰和 token。"""
        catalog = catalog or self.load()
        lines: list[str] = []
        for table in catalog.tables.values():
            columns = ", ".join(column.name for column in table.columns)
            foreign_keys = ", ".join(
                f"{key.from_column}->{key.target_table}.{key.target_column}"
                for key in table.foreign_keys
            )
            suffix = f"; foreign keys: {foreign_keys}" if foreign_keys else ""
            lines.append(
                f"- {table.name} ({table.row_count} rows): {columns}{suffix}"
            )
        return "\n".join(lines)

    def focus(
        self, table_names: list[str], catalog: SchemaCatalog | None = None
    ) -> str:
        """只展开已选表的详细 Schema 与脱敏样本。"""
        catalog = catalog or self.load()
        payload = {
            name: catalog.tables[name].model_dump(mode="json")
            for name in table_names
            if name in catalog.tables
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _redact_sample(sample: dict[str, Any]) -> dict[str, Any]:
        # 保留字段是否为空的信息，但不把真实联系方式发送给外部模型。
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_COLUMNS and value else value
            for key, value in sample.items()
        }
