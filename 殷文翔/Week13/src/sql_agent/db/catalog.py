"""数据库解析：schema、统计信息、样例与外键关系。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.engine import Engine

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForeignKey:
    constrained_table: str
    constrained_columns: list[str]
    referred_table: str
    referred_columns: list[str]

    def render(self) -> str:
        left = ", ".join(f"{self.constrained_table}.{c}" for c in self.constrained_columns)
        right = ", ".join(f"{self.referred_table}.{c}" for c in self.referred_columns)
        return f"{left} -> {right}"


@dataclass
class ColumnProfile:
    name: str
    type: str
    nullable: bool
    primary_key: bool = False
    distinct: int = 0
    missing: int = 0
    minimum: str | None = None
    maximum: str | None = None
    mode: str | None = None
    samples: list[str] = field(default_factory=list)


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: list[ColumnProfile]
    sample_rows: list[list[str]] = field(default_factory=list)


def make_readonly_url(db_path: Path) -> str:
    """生成 SQLAlchemy 使用的 SQLite 只读 URL。"""

    encoded = quote(str(Path(db_path).resolve()), safe="/")
    return f"sqlite:///file:{encoded}?mode=ro&uri=true"


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _md_cell(value: object, limit: int = 100) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "..."


class SchemaCatalog:
    """一次解析、进程内复用；按数据库文件 mtime 判断缓存是否失效。"""

    def __init__(self, db_path: Path, engine: Engine | None = None) -> None:
        self.db_path = Path(db_path).resolve()
        self.engine = engine or create_engine(make_readonly_url(self.db_path))
        self.inspector = inspect(self.engine)
        # inspector 自动排除 sqlite_sequence / sqlite_stat1 等内部表。
        self.table_names: list[str] = sorted(self.inspector.get_table_names())
        self._tables: dict[str, TableProfile] = {}
        self.foreign_keys: list[ForeignKey] = []

        with self.engine.connect() as conn:
            for table_name in self.table_names:
                table = Table(table_name, MetaData(), autoload_with=self.engine)
                row_count = conn.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                columns = [
                    self._profile_column(conn, table, column_meta)
                    for column_meta in self.inspector.get_columns(table_name)
                ]
                sample_rows = [
                    [_md_cell(value) for value in row]
                    for row in conn.execute(select(table).limit(3)).all()
                ]
                self._tables[table_name] = TableProfile(
                    name=table_name,
                    row_count=int(row_count),
                    columns=columns,
                    sample_rows=sample_rows,
                )

        seen: set[tuple] = set()
        for table_name in self.table_names:
            for fk in self.inspector.get_foreign_keys(table_name):
                edge = ForeignKey(
                    constrained_table=table_name,
                    constrained_columns=list(fk["constrained_columns"]),
                    referred_table=fk["referred_table"],
                    referred_columns=list(fk["referred_columns"]),
                )
                key = (
                    edge.constrained_table,
                    tuple(edge.constrained_columns),
                    edge.referred_table,
                    tuple(edge.referred_columns),
                )
                if key not in seen:
                    seen.add(key)
                    self.foreign_keys.append(edge)

    def _profile_column(self, conn, table: Table, meta: dict) -> ColumnProfile:
        column = table.columns[meta["name"]]
        column_type = str(meta["type"])

        def scalar(stmt, default=None):
            try:
                value = conn.execute(stmt).scalar()
                return _as_text(value) if value is not None else default
            except Exception:
                return default

        distinct = scalar(select(func.count(func.distinct(column))), 0) or 0
        missing = (
            scalar(
                select(func.count()).select_from(table).where(column.is_(None)),
                0,
            )
            or 0
        )
        minimum = scalar(select(func.min(column)))
        maximum = scalar(select(func.max(column)))

        mode = None
        if "text" in column_type.lower() or "char" in column_type.lower():
            try:
                row = conn.execute(
                    select(column, func.count().label("n"))
                    .group_by(column)
                    .order_by(func.count().desc())
                    .limit(1)
                ).first()
                mode = _as_text(row[0]) if row and row[0] is not None else None
            except Exception:
                mode = None

        samples: list[str] = []
        try:
            values = conn.execute(
                select(column).where(column.is_not(None)).limit(10)
            ).all()
            samples = list(dict.fromkeys(_as_text(v[0]) for v in values if v[0] is not None))
            samples = [s for s in samples if s is not None][:3]
        except Exception:
            samples = []

        return ColumnProfile(
            name=meta["name"],
            type=column_type,
            nullable=bool(meta.get("nullable", True)),
            primary_key=bool(meta.get("primary_key", False)),
            distinct=int(distinct),
            missing=int(missing),
            minimum=minimum,
            maximum=maximum,
            mode=mode,
            samples=samples,
        )

    def render_catalog(self) -> str:
        """给 TableSelectAgent 的轻量目录。"""

        lines: list[str] = []
        for name in self.table_names:
            profile = self._tables[name]
            columns = ", ".join(column.name for column in profile.columns)
            lines.append(f"- {name}（{profile.row_count} 行）")
            lines.append(f"  字段：{columns}")

        lines.append("")
        lines.append("外键关系：")
        lines.extend(f"- {fk.render()}" for fk in self.foreign_keys)
        return "\n".join(lines)

    def render_schema(self, tables: list[str]) -> str:
        """给 SQLGenerator / SQLRewriteAgent 的详细 schema。"""

        blocks: list[str] = []
        for name in tables:
            profile = self._tables[name]
            blocks.append(f"### {name}（{profile.row_count} 行）")
            blocks.append("")
            blocks.append("| 字段 | 类型 | 可空 | 主键 | distinct | 缺失 | min | max | mode | 样例 |")
            blocks.append("| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |")
            for column in profile.columns:
                blocks.append(
                    "| "
                    + " | ".join(
                        [
                            _md_cell(column.name),
                            _md_cell(column.type),
                            "是" if column.nullable else "否",
                            "是" if column.primary_key else "否",
                            str(column.distinct),
                            str(column.missing),
                            _md_cell(column.minimum),
                            _md_cell(column.maximum),
                            _md_cell(column.mode),
                            _md_cell(", ".join(column.samples)),
                        ]
                    )
                    + " |"
                )

            if profile.sample_rows:
                blocks.append("")
                blocks.append("样例数据：")
                headers = [column.name for column in profile.columns]
                blocks.append("| " + " | ".join(headers) + " |")
                blocks.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in profile.sample_rows:
                    blocks.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
            blocks.append("")

        edges = self.join_edges(tables)
        if edges:
            blocks.append("表之间的外键连接关系：")
            blocks.extend(f"- {edge.render()}" for edge in edges)
        else:
            blocks.append("表之间的外键连接关系：无")
        return "\n".join(blocks)

    def join_edges(self, tables: list[str]) -> list[ForeignKey]:
        selected = set(tables)
        return [
            fk
            for fk in self.foreign_keys
            if fk.constrained_table in selected and fk.referred_table in selected
        ]

    def is_connected(self, tables: list[str]) -> bool:
        if len(tables) <= 1:
            return True

        adjacency: dict[str, set[str]] = {table: set() for table in tables}
        for edge in self.join_edges(tables):
            adjacency[edge.constrained_table].add(edge.referred_table)
            adjacency[edge.referred_table].add(edge.constrained_table)

        start = tables[0]
        seen = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        return len(seen) == len(set(tables))

    def get_table_schema(self, table_name: str) -> str:
        if table_name not in self._tables:
            raise KeyError(f"未知表：{table_name}")
        return self.render_schema([table_name])

    def close(self) -> None:
        self.engine.dispose()


_catalog: SchemaCatalog | None = None
_catalog_stamp: tuple[str, float] | None = None


def get_catalog(db_path: Path | None = None) -> SchemaCatalog:
    """进程级缓存；DB 文件变化时自动重建。"""

    global _catalog, _catalog_stamp
    path = Path(db_path or settings.db_path).resolve()
    stamp = (str(path), path.stat().st_mtime)
    if _catalog is None or _catalog_stamp != stamp:
        if _catalog is not None:
            _catalog.close()
        started = time.perf_counter()
        logger.info("stage=catalog_loading db=%s", path)
        _catalog = SchemaCatalog(path)
        _catalog_stamp = stamp
        logger.info(
            "stage=catalog_ready tables=%d elapsed=%.2fs",
            len(_catalog.table_names),
            time.perf_counter() - started,
        )
        logger.debug("stage=catalog_tables names=%s", _catalog.table_names)
    else:
        logger.debug("stage=catalog_cache_hit db=%s", path)
    return _catalog
