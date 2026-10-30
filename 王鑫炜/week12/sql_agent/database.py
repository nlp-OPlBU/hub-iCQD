"""Chinook 元数据解析、SQL 编译校验和受限只读执行。仅依赖标准库。"""

import sqlite3
import time
from collections import deque
from contextlib import closing
from pathlib import Path


class QueryError(Exception):
    """SQL 无效、执行超时或结果超出约束。"""


class PolicyError(QueryError):
    """访问超出当前查询权限；这种错误不能通过改写规避。"""


TABLE_DESCRIPTIONS = {
    "albums": "专辑，一行一张专辑；关联 artists 和 tracks",
    "artists": "艺术家，一行一个艺术家；不保证每位艺术家都有专辑",
    "customers": "客户，可按 Country 分析；联系人字段禁止读取",
    "employees": "员工及汇报关系；联系人和生日字段禁止读取",
    "genres": "音乐流派字典",
    "invoice_items": "发票明细；销售额 = UnitPrice * Quantity，销量 = SUM(Quantity)",
    "invoices": "发票头；销售额 = SUM(Total)，日期 InvoiceDate，国家 BillingCountry",
    "media_types": "媒体类型字典",
    "playlist_track": "播放列表和曲目的多对多关联表",
    "playlists": "播放列表",
    "tracks": "曲目目录；UnitPrice 是目录单价，不代表实际销售额",
}

# 教学示例的列级权限。生产系统需要用户身份和行级策略。
BLOCKED_COLUMNS = {
    "email", "phone", "fax", "address", "birthdate", "billingaddress",
    "postalcode", "billingpostalcode",
}
ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "total", "min", "max", "round", "abs",
    "coalesce", "ifnull", "nullif", "lower", "upper", "length", "trim",
    "ltrim", "rtrim", "substr", "substring", "replace", "instr", "like",
    "glob", "date", "datetime", "strftime", "julianday", "unixepoch",
    "row_number", "rank", "dense_rank", "lag", "lead",
}


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class Database:
    def __init__(self, path, max_rows=50, timeout_seconds=2.0, max_steps=2_000_000):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise ValueError("数据库不存在：{}".format(self.path))
        if not 1 <= max_rows <= 1000 or timeout_seconds <= 0 or max_steps <= 0:
            raise ValueError("行数必须在 1～1000 内，超时和指令预算必须为正数")
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.max_steps = max_steps

    def _connect(self):
        # mode=ro 不会误创建数据库；query_only 再限制连接的写入能力。
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                               timeout=self.timeout_seconds, cached_statements=0)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        if hasattr(conn, "setlimit"):  # Python 3.11+ 额外约束单个 SQLite 值的长度。
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
        return conn

    def parse(self):
        """元数据来自数据库，不向模型发送客户或员工的原始样本。"""
        tables = {}
        with closing(self._connect()) as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for name in names:
                quoted = quote_identifier(name)
                columns = [{"name": r[1], "type": r[2], "nullable": not bool(r[3]),
                            "primary_key": bool(r[5]),
                            "readable": r[1].lower() not in BLOCKED_COLUMNS}
                           for r in conn.execute("PRAGMA table_info({})".format(quoted))]
                foreign_keys = [{"column": r[3], "target_table": r[2], "target_column": r[4]}
                                for r in conn.execute("PRAGMA foreign_key_list({})".format(quoted))]
                tables[name] = {"description": TABLE_DESCRIPTIONS.get(name, "业务表"),
                                "columns": columns, "foreign_keys": foreign_keys,
                                "row_count": conn.execute(
                                    "SELECT COUNT(*) FROM " + quoted).fetchone()[0]}
                if name == "invoices" and any(c["name"] == "InvoiceDate" for c in columns):
                    low, high = conn.execute(
                        "SELECT MIN(InvoiceDate), MAX(InvoiceDate) FROM invoices").fetchone()
                    tables[name]["date_range"] = {"column": "InvoiceDate", "min": low, "max": high}
        return tables

    @staticmethod
    def expand_join_tables(selected, schema):
        """沿外键最短路径补齐连接表，避免只选择两端而遗漏中间表。"""
        if not selected or any(t not in schema for t in selected):
            raise QueryError("表选择为空或包含不存在的表")
        graph = {t: set() for t in schema}
        for table, info in schema.items():
            for fk in info["foreign_keys"]:
                other = fk["target_table"]
                if other in graph:
                    graph[table].add(other)
                    graph[other].add(table)
        expanded = set(selected)
        # 将每个表连接到第一个表；排序保证演示和测试可复现。
        root = selected[0]
        for target in selected[1:]:
            queue = deque([(root, [root])])
            seen = {root}
            while queue:
                node, path = queue.popleft()
                if node == target:
                    expanded.update(path)
                    break
                for neighbor in sorted(graph[node]):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, path + [neighbor]))
            else:
                raise QueryError("所选表之间没有外键路径，请明确关联方式")
        return sorted(expanded)

    def _guard(self, conn, tables):
        allowed = {t.lower() for t in tables}
        denied = []

        def authorize(action, arg1, arg2, db_name, source):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                # SQLite 对 COUNT(*) 等无需读取具体列的操作可能给出 db_name=None。
                if ((arg1 or "").lower() in allowed
                        and (db_name == "main" or (db_name is None and arg2 == ""))
                        and (arg2 or "").lower() not in BLOCKED_COLUMNS):
                    return sqlite3.SQLITE_OK
                denied.append("禁止访问 {}.{}".format(arg1, arg2))
            elif action == sqlite3.SQLITE_FUNCTION:
                if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                    return sqlite3.SQLITE_OK
                denied.append("禁止调用函数 {}".format(arg2))
            else:
                denied.append("仅允许读取业务表，禁止写入、附件数据库和 PRAGMA")
            return sqlite3.SQLITE_DENY

        deadline = time.monotonic() + self.timeout_seconds
        ticks = [0]

        def interrupt():
            ticks[0] += 1000
            return int(time.monotonic() >= deadline or ticks[0] >= self.max_steps)

        conn.set_authorizer(authorize)
        conn.set_progress_handler(interrupt, 1000)
        return denied

    def _query(self, sql, tables, explain):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000:
            raise QueryError("SQL 必须是 1～20000 字符的非空字符串")
        with closing(self._connect()) as conn:
            denied = self._guard(conn, tables)
            try:
                # EXPLAIN QUERY PLAN 编译查询，不运行查询来做校验。
                cursor = conn.execute(("EXPLAIN QUERY PLAN " if explain else "") + sql)
                if cursor.description is None:
                    raise PolicyError("必须返回查询结果")
                if explain:
                    return {"valid": True, "plan": [list(r) for r in cursor.fetchmany(100)]}
                rows = []
                size = 0
                truncated = False
                for _ in range(self.max_rows + 1):
                    row = cursor.fetchone()
                    if row is None:
                        break
                    if len(rows) == self.max_rows:
                        truncated = True
                        break
                    if any(isinstance(v, bytes) for v in row):
                        raise QueryError("不支持输出二进制字段")
                    size += sum(len(str(v).encode("utf-8")) for v in row)
                    if size > 64_000:
                        raise QueryError("结果超过 64000 字节，请缩小查询范围")
                    rows.append(list(row))
                return {"columns": [c[0] for c in cursor.description], "rows": rows,
                        "returned_rows": len(rows), "truncated": truncated,
                        "row_limit": self.max_rows}
            except (sqlite3.Error, sqlite3.Warning) as exc:
                if denied:
                    raise PolicyError(denied[0]) from exc
                raise QueryError(str(exc)) from exc

    def validate(self, sql, tables):
        return self._query(sql, tables, explain=True)

    def execute(self, sql, tables):
        # 执行时再次施加权限与预算，不依赖先前的校验结果。
        return self._query(sql, tables, explain=False)
