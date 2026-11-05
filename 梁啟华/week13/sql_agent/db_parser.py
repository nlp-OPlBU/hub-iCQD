"""数据库解析模块。

负责读取 SQLite 数据库的结构信息（表、字段、类型、主键、外键）、
字段统计信息（唯一值、空值、极值、常见值）以及样例数据，
并为后续 Agent 生成可直接放入 Prompt 的 schema 文本。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import traceback
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# 只读查询允许的关键字
READ_ONLY_KEYWORDS = {"SELECT", "WITH"}
# 出现即判定为非只读的 SQL 关键字
FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "VACUUM",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "REINDEX",
)


def _quote(name: str) -> str:
    """为 SQLite 标识符加双引号，避免关键字/特殊字符冲突。"""
    return '"' + name.replace('"', '""') + '"'


def _short(value: Any, limit: int = 32) -> str:
    """把任意值转成适合表格展示的短字符串。"""
    text = "NULL" if value is None else str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _render_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    """把二维数据渲染为 Markdown 风格表格，用于 Prompt 输入。"""
    headers = [str(h) for h in headers]
    body = [[_short(cell, 40) for cell in row] for row in rows]
    widths = [
        max([len(headers[i])] + [len(row[i]) for row in body])
        for i in range(len(headers))
    ]

    def _line(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"

    lines = [_line(headers), "| " + " | ".join("-" * w for w in widths) + " |"]
    lines.extend(_line(row) for row in body)
    return "\n".join(lines)


def _strip_literals(sql: str) -> str:
    """去掉字符串字面量与注释，只保留 SQL 结构性内容。"""
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sql = re.sub(r'"(?:""|[^"])*"', '""', sql)
    sql = re.sub(r"`[^`]*`", "``", sql)
    sql = re.sub(r"\[[^\]]*\]", "[]", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _count_statements(sql: str) -> int:
    """统计 SQL 语句条数（忽略字符串内的分号）。"""
    cleaned = _strip_literals(sql)
    return len([part for part in cleaned.split(";") if part.strip()])


def normalize_sql(sql: str) -> str:
    """清理 LLM 输出中多余的代码块围栏、前缀与结尾分号。"""
    text = (sql or "").strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1)
    text = re.sub(r"^\s*sql\s*[:：]\s*", "", text, flags=re.I)
    text = text.strip().strip("`").strip()
    # 模型偶尔会在 SQL 前加解释文字，从第一个 SELECT/WITH 开始截取
    match = re.search(r"\b(SELECT|WITH)\b", text, flags=re.I)
    if match and match.start() > 0:
        text = text[match.start() :]
    while text.endswith(";"):
        text = text[:-1].rstrip()
    text = text.strip().strip("`").strip()
    return text


class DBParser:
    """SQLite 数据库解析器。

    参数:
        db_path: SQLite 数据库文件路径。
        include_system_tables: 是否包含 sqlite_ 开头的系统表，默认不包含。
        sample_rows: 每张表保留的样例行数。
    """

    def __init__(
        self,
        db_path: str | Path,
        include_system_tables: bool = False,
        sample_rows: int = 3,
    ) -> None:
        self.db_path = str(Path(db_path).expanduser().resolve())
        if not Path(self.db_path).is_file():
            raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.include_system_tables = include_system_tables
        self.sample_rows = sample_rows

        self.table_names: list[str] = []
        self.foreign_keys: list[dict[str, Any]] = []
        self._table_fields: dict[str, dict[str, dict[str, Any]]] = {}
        self._table_sample: dict[str, list[dict[str, Any]]] = {}
        self._row_counts: dict[str, int] = {}

        self._analyze()

    # ------------------------------------------------------------------ 解析
    def _analyze(self) -> None:
        """遍历数据库中的表，收集字段、外键和样例数据。"""
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        all_tables = [row[0] for row in cursor.fetchall()]
        if not self.include_system_tables:
            all_tables = [t for t in all_tables if not t.startswith("sqlite_")]
        self.table_names = all_tables

        for table_name in self.table_names:
            logger.info("解析数据表：%s", table_name)
            self._table_fields[table_name] = {}
            self._row_counts[table_name] = self._fetch_count(table_name)
            self._collect_foreign_keys(table_name)
            self._collect_columns(table_name)
            self._collect_sample(table_name)

    def _fetch_count(self, table_name: str) -> int:
        try:
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM {_quote(table_name)}"
            ).fetchone()
            return int(row[0])
        except sqlite3.Error:
            logger.warning("统计表 %s 行数失败", table_name, exc_info=True)
            return 0

    def _collect_foreign_keys(self, table_name: str) -> None:
        """通过 PRAGMA foreign_key_list 收集外键关系。"""
        try:
            rows = self.conn.execute(
                f"PRAGMA foreign_key_list({_quote(table_name)})"
            ).fetchall()
        except sqlite3.Error:
            logger.warning("读取表 %s 外键失败", table_name, exc_info=True)
            return

        # 复合外键会拆成多行，按 id 聚合
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            fk_id = row["id"]
            item = grouped.setdefault(
                fk_id,
                {
                    "constrained_table": table_name,
                    "constrained_columns": [],
                    "referred_table": row["table"],
                    "referred_columns": [],
                },
            )
            item["constrained_columns"].append(row["from"])
            item["referred_columns"].append(row["to"])
        self.foreign_keys.extend(grouped.values())

    def _collect_columns(self, table_name: str) -> None:
        columns = self.conn.execute(
            f"PRAGMA table_info({_quote(table_name)})"
        ).fetchall()
        for column in columns:
            name = column["name"]
            decl_type = column["type"] or ""
            meta: dict[str, Any] = {
                "name": name,
                "type": decl_type or "-",
                "nullable": not bool(column["notnull"]),
                "default": column["dflt_value"],
                "primary_key": bool(column["pk"]),
            }
            meta.update(self._column_stats(table_name, name, decl_type))
            self._table_fields[table_name][name] = meta

    def _column_stats(
        self, table_name: str, column_name: str, decl_type: str
    ) -> dict[str, Any]:
        """统计字段的唯一值、空值、极值、常见值和样例值。"""
        table = _quote(table_name)
        column = _quote(column_name)
        stats: dict[str, Any] = {
            "distinct": None,
            "nan_count": None,
            "min": None,
            "max": None,
            "mode": None,
            "examples": [],
        }

        try:
            row = self.conn.execute(
                f"SELECT COUNT(DISTINCT {column}), "
                f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END), "
                f"MIN({column}), MAX({column}) FROM {table}"
            ).fetchone()
            stats.update(
                {
                    "distinct": row[0],
                    "nan_count": row[1] or 0,
                    "min": row[2],
                    "max": row[3],
                }
            )
        except sqlite3.Error:
            logger.debug("统计字段 %s.%s 失败", table_name, column_name, exc_info=True)

        upper_type = decl_type.upper()
        if any(token in upper_type for token in ("CHAR", "TEXT", "CLOB", "VARCHAR")):
            try:
                mode_row = self.conn.execute(
                    f"SELECT {column}, COUNT(*) AS c FROM {table} "
                    f"WHERE {column} IS NOT NULL "
                    f"GROUP BY {column} ORDER BY c DESC LIMIT 1"
                ).fetchone()
                stats["mode"] = mode_row[0] if mode_row else None
            except sqlite3.Error:
                logger.debug("统计字段 %s.%s 常见值失败", table_name, column_name)

        try:
            rows = self.conn.execute(
                f"SELECT DISTINCT {column} FROM {table} "
                f"WHERE {column} IS NOT NULL LIMIT 3"
            ).fetchall()
            stats["examples"] = [row[0] for row in rows]
        except sqlite3.Error:
            logger.debug("采样字段 %s.%s 失败", table_name, column_name)

        return stats

    def _collect_sample(self, table_name: str) -> None:
        try:
            rows = self.conn.execute(
                f"SELECT * FROM {_quote(table_name)} LIMIT {int(self.sample_rows)}"
            ).fetchall()
            self._table_sample[table_name] = [dict(row) for row in rows]
        except sqlite3.Error:
            logger.warning("采样表 %s 失败", table_name, exc_info=True)
            self._table_sample[table_name] = []

    # ------------------------------------------------------------- 查询接口
    def get_row_count(self, table_name: str) -> int:
        return self._row_counts.get(table_name, 0)

    def get_table_fields(self, table_name: str) -> list[dict[str, Any]]:
        """返回字段统计信息列表。"""
        self._ensure_table(table_name)
        return list(self._table_fields[table_name].values())

    def get_data_relations(self) -> list[dict[str, Any]]:
        """返回数据库外键（表连接）关系。"""
        return self.foreign_keys

    def get_table_sample(self, table_name: str) -> list[dict[str, Any]]:
        """返回表样例数据。"""
        self._ensure_table(table_name)
        return self._table_sample.get(table_name, [])

    def _ensure_table(self, table_name: str) -> None:
        if table_name not in self._table_fields:
            raise KeyError(f"表不存在：{table_name}")

    # ------------------------------------------------------------- 文本渲染
    def get_table_fields_text(self, table_name: str) -> str:
        """渲染单表字段统计信息。"""
        rows = []
        for field in self.get_table_fields(table_name):
            rows.append(
                [
                    field["name"],
                    field["type"],
                    "是" if field["primary_key"] else "",
                    field["distinct"],
                    field["nan_count"],
                    field["min"],
                    field["max"],
                    field["mode"],
                ]
            )
        return _render_table(
            ["字段", "类型", "主键", "唯一值数", "空值数", "最小值", "最大值", "常见值"],
            rows,
        )

    def get_table_sample_text(self, table_name: str) -> str:
        """渲染单表样例数据。"""
        sample = self.get_table_sample(table_name)
        if not sample:
            return "(无样例数据)"
        columns = list(sample[0].keys())
        return _render_table(columns, [[row[col] for col in columns] for row in sample])

    def get_data_relations_text(self, tables: list[str] | None = None) -> str:
        """渲染外键关系，可按表过滤。"""
        relations = self.foreign_keys
        if tables:
            selected = set(tables)
            relations = [
                fk
                for fk in relations
                if fk["constrained_table"] in selected
                and fk["referred_table"] in selected
            ]
        if not relations:
            return "(无外键关系)"
        return "\n".join(
            f"- {fk['constrained_table']}.{','.join(fk['constrained_columns'])} "
            f"-> {fk['referred_table']}.{','.join(fk['referred_columns'])}"
            for fk in relations
        )

    def get_compact_schema_text(self, tables: list[str] | None = None) -> str:
        """渲染精简 schema（表名 + 行数 + 字段），用于 table 选择。"""
        names = tables or self.table_names
        lines = []
        for table_name in names:
            fields = self.get_table_fields(table_name)
            columns = ", ".join(
                f"{f['name']} {f['type']}" + ("[PK]" if f["primary_key"] else "")
                for f in fields
            )
            lines.append(f"- {table_name}（{self.get_row_count(table_name)} 行）: {columns}")
        return "\n".join(lines)

    def get_schema_text(
        self,
        tables: list[str] | None = None,
        include_sample: bool = True,
    ) -> str:
        """渲染完整的 Prompt schema，包含字段统计、样例与外键。"""
        names = tables or self.table_names
        blocks: list[str] = []
        for table_name in names:
            self._ensure_table(table_name)
            block = [
                f"### 表 {table_name}（共 {self.get_row_count(table_name)} 行）",
                self.get_table_fields_text(table_name),
            ]
            if include_sample:
                block.extend(["", "样例数据：", self.get_table_sample_text(table_name)])
            fk_text = self.get_data_relations_text([table_name])
            if fk_text != "(无外键关系)":
                block.extend(["", "外键：", fk_text])
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    def get_schema_overview(self) -> str:
        """完整库的紧凑结构 + 外键关系，用于 table 选择。"""
        return (
            f"候选表：\n{self.get_compact_schema_text()}\n\n"
            f"表间关系：\n{self.get_data_relations_text()}"
        )

    # ------------------------------------------------------------- SQL 能力
    def check_sql(self, sql: str) -> tuple[bool, str]:
        """校验 SQL 是否可执行且为单条只读查询。

        返回:
            (是否通过, 错误信息或 "ok")
        """
        sql = normalize_sql(sql)
        if not sql:
            return False, "SQL 为空"

        if _count_statements(sql) > 1:
            return False, "只允许执行单条 SQL 语句"

        violation = self._read_only_violation(sql)
        if violation:
            return False, violation

        try:
            # EXPLAIN 只做语法与结构校验，不真正执行查询
            self.conn.execute("EXPLAIN QUERY PLAN " + sql)
        except Exception:
            return False, traceback.format_exc(limit=1).strip()

        return True, "ok"

    def execute_sql(self, sql: str, max_rows: int = 200) -> dict[str, Any]:
        """执行只读 SQL，返回列名、数据行和截断标记。"""
        sql = normalize_sql(sql)
        violation = self._read_only_violation(sql)
        if violation:
            raise ValueError(violation)

        cursor = self.conn.execute(sql)
        columns = [item[0] for item in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        data = [list(row) for row in rows[:max_rows]]
        return {
            "columns": columns,
            "rows": data,
            "row_count": len(data),
            "truncated": truncated,
            "max_rows": max_rows,
        }

    def _read_only_violation(self, sql: str) -> str | None:
        cleaned = _strip_literals(sql)
        match = re.match(r"\s*([A-Za-z_]+)", cleaned)
        if not match:
            return "无法识别 SQL 关键字"
        if match.group(1).upper() not in READ_ONLY_KEYWORDS:
            return "仅允许执行 SELECT 查询"
        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", cleaned, flags=re.I):
                return f"检测到非只读关键字：{keyword}"
        return None

    # --------------------------------------------------------------- 生命周期
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DBParser":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
