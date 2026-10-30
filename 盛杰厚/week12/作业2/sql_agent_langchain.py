#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL Agent（LangChain 版）—— 用 LangChain 复刻 week11/作业1/sql_agent.py

与原版（直接调用 openai SDK）相比，本版改用 LangChain 的能力：
  - ChatOpenAI                代替 OpenAI(...) 客户端（同样兼容 DeepSeek 等 OpenAI 兼容接口）
  - PromptTemplate            代替手写 f-string 模板
  - LCEL 链（| 管道）          代替手写 llm() 函数，把 prompt -> llm -> parser 串成一条链
  - StrOutputParser           自动抽取纯文本输出
  - JsonOutputParser + Pydantic 让「表选择」步骤返回结构化结果（字典，而非自己 split 逗号）

工作流步骤保持不变：
  1. 数据库解析   DBParser         —— 解析表结构 / 字段 / 外键 / 样例 / 统计信息
  2. 表选择       select_tables    —— 结构化输出，返回 {"tables": [...]}
  3. SQL 生成     generate_sql     —— 结合表结构生成 SQL
  4. SQL 校验     validate_sql     —— 编译校验（语法 / 表列引用，不执行）
  5. SQL 改写     rewrite_sql      —— 校验失败时结合报错信息改写 SQL
  6. SQL 执行     execute_sql      —— 真正执行 SQL 并返回结果
  7. 结果汇总     summarize        —— 将结果组织为自然语言回答

依赖: sqlalchemy>=2.0, pandas, langchain, langchain-openai
用法:
    export DEEPSEEK_API_KEY=你的key   # 先设置环境变量
    python sql_agent_langchain.py "每个客户的消费总额是多少？"
    DEEPSEEK_API_KEY=xxx DEEPSEEK_MODEL=deepseek-flash python sql_agent_langchain.py "每个流派有多少首歌？"
"""

import os
import re
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM 配置（OpenAI 兼容接口，默认指向 DeepSeek）
# API Key 只从环境变量读取，绝不硬编码在源码里。
# ---------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

# 数据库文件默认与本脚本放在同一目录下
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "chinook.db"


# ---------------------------------------------------------------------------
# 步骤 1：数据库解析（纯 SQLAlchemy，与 LLM 无关，原样保留）
# ---------------------------------------------------------------------------
class DBParser:
    """解析数据库：表名、字段、主外键、统计信息与数据样例。

    db_url 支持：
        sqlite: sqlite:///./chinook.db
        mysql:  mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
    """

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        # SQLAlchemy 的 get_table_names() 已经过滤掉 sqlite_sequence / sqlite_stat1 等内部表
        self.table_names: List[str] = self.inspector.get_table_names()

        self.foreign_keys: List[Dict[str, Any]] = []
        self._table_fields: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._table_sample: Dict[str, pd.DataFrame] = {}

        metadata = MetaData()
        # 只建立一次连接，逐表统计字段信息
        with self.engine.connect() as conn:
            for table_name in self.table_names:
                print("Table ->", table_name)

                # 累计外键
                for fk in self.inspector.get_foreign_keys(table_name):
                    self.foreign_keys.append(
                        {
                            "constrained_table": table_name,
                            "constrained_columns": fk["constrained_columns"],
                            "referred_table": fk["referred_table"],
                            "referred_columns": fk["referred_columns"],
                        }
                    )

                # 主键列（SQLAlchemy 2.0 的 get_columns 不再带 primary_key，需单独取）
                pk_columns = set()
                pk = self.inspector.get_pk_constraint(table_name)
                pk_columns.update(pk.get("constrained_columns", []))

                table_instance = Table(table_name, metadata, autoload_with=self.engine)
                table_columns = self.inspector.get_columns(table_name)

                fields: Dict[str, Dict[str, Any]] = {}
                for column_meta in table_columns:
                    name = column_meta["name"]
                    # SQLAlchemy 2.0 中通过下标访问列，不再支持 getattr(columns, name)
                    column_instance = table_instance.c[name]

                    info: Dict[str, Any] = {
                        "name": name,
                        "type": str(column_meta["type"]),
                        "nullable": column_meta.get("nullable", True),
                        "default": column_meta.get("default"),
                        "primary_key": name in pk_columns,
                    }

                    # distinct 取值个数
                    info["distinct"] = conn.execute(
                        select(func.count(func.distinct(column_instance))).select_from(table_instance)
                    ).scalar()

                    # 文本/字符字段统计众数（most frequent value）
                    col_type = str(column_meta["type"]).lower()
                    if any(k in col_type for k in ("text", "char", "varchar")):
                        info["mode"] = conn.execute(
                            select(column_instance, func.count().label("cnt"))
                            .select_from(table_instance)
                            .group_by(column_instance)
                            .order_by(func.count().desc())
                            .limit(1)
                        ).scalar()
                    else:
                        info["mode"] = None

                    # 空值个数
                    info["nan_count"] = conn.execute(
                        select(func.count()).select_from(table_instance).where(column_instance.is_(None))
                    ).scalar()

                    # max / min
                    info["max"] = conn.execute(select(func.max(column_instance)).select_from(table_instance)).scalar()
                    info["min"] = conn.execute(select(func.min(column_instance)).select_from(table_instance)).scalar()

                    # 任意取值（去重后取前 3 个，供大模型理解字段含义）
                    values = conn.execute(select(column_instance).select_from(table_instance).limit(10)).scalars().all()
                    info["random"] = list(dict.fromkeys(str(v) for v in values if v is not None))[:3]

                    fields[name] = info

                self._table_fields[table_name] = fields

                # 表样例（第一行）
                first_row = conn.execute(select(table_instance).limit(1)).first()
                df = pd.DataFrame([tuple(first_row)] if first_row else [], columns=[c["name"] for c in table_columns])
                self._table_sample[table_name] = df

    # ---------------------------- 查询辅助方法 ----------------------------
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库链接信息（主键和外键）"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取数据表样例"""
        return self._table_sample[table_name]

    # ---------------------------- 供大模型使用的文本描述 ----------------------------
    def describe_relations(self) -> str:
        """外键关系描述，形如 invoices.CustomerId -> customers.CustomerId"""
        lines = []
        for fk in self.foreign_keys:
            for i, col in enumerate(fk["constrained_columns"]):
                referred = fk["referred_columns"][i] if i < len(fk["referred_columns"]) else "?"
                lines.append(f"{fk['constrained_table']}.{col} -> {fk['referred_table']}.{referred}")
        return "\n".join(lines)

    def describe_schema(self, tables: List[str], include_samples: bool = True) -> str:
        """生成某几张表的紧凑 schema 描述（供表选择 / SQL 生成使用）"""
        blocks = []
        for table_name in tables:
            if table_name not in self._table_fields:
                continue
            lines = [f"### 表 {table_name}"]
            for name, info in self._table_fields[table_name].items():
                flags = []
                if info["primary_key"]:
                    flags.append("主键")
                # 标注外键
                for fk in self.foreign_keys:
                    if fk["constrained_table"] == table_name and name in fk["constrained_columns"]:
                        referred = fk["referred_columns"][0]
                        flags.append(f"外键->{fk['referred_table']}.{referred}")
                        break
                suffix = (" " + ",".join(flags)) if flags else ""
                if include_samples and info.get("random"):
                    suffix += f" 示例值: {info['random']}"
                lines.append(f"- {name} ({info['type']}){suffix}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    # ---------------------------- SQL 编译校验 / 执行 ----------------------------
    def compile_check(self, sql: str) -> Tuple[bool, str]:
        """检查 SQL 是否“可编译”（语法 + 表/列引用合法），只编译、不执行。

        注意：这里校验的是“语法/结构正确”，不是“运行结果正确”：
          - 能捕获：语法错误、表/列不存在（prepare 阶段的 schema 解析）
          - 不真正执行：对 INSERT/UPDATE/DELETE 无副作用，对 SELECT 无查询开销
          - 局限：无法捕获运行时错误（如除零）和“结果是否真的正确”——
            前者在实际执行阶段处理，后者需要 ground truth 评测
        其他数据库（如 MySQL）没有等价的通用编译检查，退化为直接执行。

        返回: (是否通过编译校验, 报错信息)
        """
        statement = sql.strip().rstrip(";").strip()
        if self.db_type == "sqlite":
            statement = f"EXPLAIN {statement}"
        try:
            with self.engine.connect() as conn:
                conn.execute(text(statement))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> Tuple[List[str], List[tuple]]:
        """执行 SQL，返回 (列名列表, 行列表)"""
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(r) for r in result.fetchall()]
            return columns, rows


# ---------------------------------------------------------------------------
# 表选择的结构化输出：让大模型按固定 JSON 结构返回，避免手动 split 逗号
# ---------------------------------------------------------------------------
class TableSelection(BaseModel):
    """回答用户问题所需的数据表集合。"""

    tables: List[str] = Field(description="回答该问题所需的数据表名列表，可以多选，也可以只选一张")


# ---------------------------------------------------------------------------
# SQL Agent（LangChain 版）：表选择 -> SQL 生成 -> 校验 -> 改写 -> 执行 -> 汇总
# ---------------------------------------------------------------------------
class SQLAgent:
    def __init__(
        self,
        db_url: str,
        api_key: str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEFAULT_MODEL,
        max_retry: int = 5,
    ) -> None:
        self.parser = DBParser(db_url)
        if not api_key:
            raise ValueError(
                "缺少 API Key：请先设置环境变量 DEEPSEEK_API_KEY（或传入 api_key 参数）。"
            )
        self.model = model
        self.max_retry = max_retry

        # LangChain 的 ChatOpenAI：OpenAI 兼容接口，默认指向 DeepSeek。
        # 推理步骤（表选择/SQL 生成/SQL 改写）用低温度，汇总用稍高温度更自然。
        self.llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, temperature=0.1)
        self.summary_llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, temperature=0.6)

        # ------------------------------------------------------------------
        # 步骤 2：表选择 —— 结构化输出（JsonOutputParser + Pydantic）
        # ------------------------------------------------------------------
        self._table_select_parser = JsonOutputParser(pydantic_object=TableSelection)
        self._table_select_prompt = PromptTemplate(
            template=(
                "你是一个数据库专家。请根据用户提问，从下面的数据库中选择回答该问题所需的数据表"
                "（可以多选，也可以只选一张）。\n\n"
                "数据库中的所有表名：{table_names}\n\n"
                "表之间的外键关系：\n{relations}\n\n"
                "每张表的字段结构：\n{schema}\n\n"
                "用户提问：{question}\n\n"
                "{format_instructions}"
            ),
            input_variables=["table_names", "relations", "schema", "question"],
            partial_variables={"format_instructions": self._table_select_parser.get_format_instructions()},
        )
        # LCEL 链：prompt -> llm -> json parser，invoke 后直接得到 {"tables": [...]}
        self._table_select_chain = self._table_select_prompt | self.llm | self._table_select_parser

        # ------------------------------------------------------------------
        # 步骤 3：SQL 生成
        # ------------------------------------------------------------------
        self._sql_gen_prompt = PromptTemplate(
            template=(
                "你是一个数据库专家。请根据用户提问和表结构，生成一条能在 SQLite 上直接执行的 SQL 语句。\n\n"
                "用户提问：{question}\n\n"
                "可用的表及其字段结构：\n{schema}\n\n"
                "表之间的外键关系：\n{relations}\n\n"
                "请直接输出 SQL 语句（不要用 markdown 代码块，不要输出任何解释）。"
            ),
            input_variables=["question", "schema", "relations"],
        )
        self._sql_gen_chain = self._sql_gen_prompt | self.llm | StrOutputParser()

        # ------------------------------------------------------------------
        # 步骤 5：SQL 改写
        # ------------------------------------------------------------------
        self._sql_rewrite_prompt = PromptTemplate(
            template=(
                "你是一个数据库专家。下面这条 SQL 校验/执行时报错，请结合报错信息修正后重新输出。\n\n"
                "用户提问：{question}\n\n"
                "表结构：\n{schema}\n\n"
                "出错的 SQL：\n{sql}\n\n"
                "报错信息：\n{error}\n\n"
                "请直接输出修正后的 SQL 语句（不要用 markdown 代码块，不要输出任何解释）。"
            ),
            input_variables=["question", "schema", "sql", "error"],
        )
        self._sql_rewrite_chain = self._sql_rewrite_prompt | self.llm | StrOutputParser()

        # ------------------------------------------------------------------
        # 步骤 7：结果汇总
        # ------------------------------------------------------------------
        self._summary_prompt = PromptTemplate(
            template=(
                "你是答案汇总专家。请把用户提问、执行的 SQL 和执行结果组织为自然语言回答。\n\n"
                "用户提问：{question}\n\n"
                "执行 SQL：{sql}\n\n"
                "执行结果：\n{result}\n\n"
                "请直接输出自然语言回答。"
            ),
            input_variables=["question", "sql", "result"],
        )
        self._summary_chain = self._summary_prompt | self.summary_llm | StrOutputParser()

    # ---------------------------- 大模型调用（统一异常处理） ----------------------------
    @staticmethod
    def _invoke_chain(chain, inputs: Dict[str, Any]) -> Optional[Any]:
        """invoke 一条 LCEL 链；失败时打印并返回 None，而不是让整个流程崩掉。"""
        try:
            return chain.invoke(inputs)
        except Exception as e:
            print(f"[LLM 调用失败] {e}")
            return None

    @staticmethod
    def _extract_sql(raw: Optional[str]) -> str:
        """从大模型输出中抽取纯 SQL（去掉 markdown 代码块 / 多余前缀）。"""
        if not raw:
            return ""
        raw = raw.strip()
        m = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
        # 去掉类似 "sql\nSELECT ..." 的前缀
        raw = re.sub(r"^\s*sql\s*", "", raw, flags=re.IGNORECASE).strip()
        return raw

    # ---------------------------- 步骤 2：表选择 ----------------------------
    def select_tables(self, question: str) -> List[str]:
        table_names = ", ".join(self.parser.table_names)
        relations = self.parser.describe_relations()
        # 表选择阶段只给字段结构（不含样例），信息足够且更省 token
        schema = self.parser.describe_schema(self.parser.table_names, include_samples=False)

        result = self._invoke_chain(
            self._table_select_chain,
            {"table_names": table_names, "relations": relations, "schema": schema, "question": question},
        )
        print(f"[表选择] 原始输出: {result}")

        # 从结构化结果里取表名，只保留真实存在的表，非法输出回退为全部表
        tables = result.get("tables", []) if isinstance(result, dict) else []
        selected = []
        for name in tables:
            if isinstance(name, str) and name in self.parser.table_names and name not in selected:
                selected.append(name)
        if not selected:
            print("[表选择] 未解析出有效表名，回退为全部表")
            selected = list(self.parser.table_names)
        return selected

    # ---------------------------- 步骤 3：SQL 生成 ----------------------------
    def generate_sql(self, question: str, tables: List[str]) -> str:
        schema = self.parser.describe_schema(tables, include_samples=True)
        relations = self.parser.describe_relations()
        raw = self._invoke_chain(
            self._sql_gen_chain, {"question": question, "schema": schema, "relations": relations}
        )
        sql = self._extract_sql(raw)
        print(f"[SQL 生成]\n{sql}")
        return sql

    # ---------------------------- 步骤 4：SQL 校验（只编译、不执行） ----------------------------
    def validate_sql(self, sql: str) -> Tuple[bool, str]:
        """校验 SQL 语法与表/列引用是否合法（编译期校验，不真正执行）。"""
        return self.parser.compile_check(sql)

    # ---------------------------- 步骤 5：SQL 改写 ----------------------------
    def rewrite_sql(self, question: str, sql: str, error: str) -> str:
        schema = self.parser.describe_schema(self.parser.table_names, include_samples=True)
        raw = self._invoke_chain(
            self._sql_rewrite_chain, {"question": question, "schema": schema, "sql": sql, "error": error}
        )
        new_sql = self._extract_sql(raw)
        print(f"[SQL 改写]\n{new_sql}")
        return new_sql

    # ---------------------------- 步骤 6：SQL 执行 ----------------------------
    def execute_sql(self, sql: str) -> Tuple[List[str], List[tuple]]:
        return self.parser.execute_sql(sql)

    # ---------------------------- 步骤 7：结果汇总 ----------------------------
    def summarize(self, question: str, sql: str, columns: List[str], rows: List[tuple]) -> str:
        result = self._format_result(columns, rows)
        answer = self._invoke_chain(self._summary_chain, {"question": question, "sql": sql, "result": result})
        return answer or ""

    @staticmethod
    def _format_result(columns: List[str], rows: List[tuple], max_rows: int = 200) -> str:
        if not rows:
            return "(无结果)"
        header = " | ".join(columns)
        body = [" | ".join("" if v is None else str(v) for v in row) for row in rows[:max_rows]]
        out = [header] + body
        if len(rows) > max_rows:
            out.append(f"... (共 {len(rows)} 行，仅展示前 {max_rows} 行)")
        return "\n".join(out)

    # ---------------------------- 入口：编排完整工作流 ----------------------------
    def action(self, question: str, verbose: bool = True) -> Dict[str, Any]:
        if verbose:
            print("=" * 60)
            print(f"提问: {question}")
            print("=" * 60)

        # 2. 表选择
        tables = self.select_tables(question)
        print(f"[表选择] -> {tables}")

        # 3. SQL 生成
        sql = self.generate_sql(question, tables)

        # 4-6. 校验（只编译）-> 改写 -> 执行（含运行时错误改写）循环
        columns: List[str] = []
        rows: List[tuple] = []
        validated = False
        for retry in range(self.max_retry):
            # 4. 校验：只编译不执行，捕获语法/表/列错误
            ok, err = self.validate_sql(sql)
            if not ok:
                print(f"[SQL 校验] 第 {retry + 1} 次编译失败，尝试改写")
                sql = self.rewrite_sql(question, sql, err)
                if not sql:
                    break
                continue

            # 6. 执行：真正运行（运行时错误也回退到改写）
            try:
                columns, rows = self.execute_sql(sql)
                validated = True
                print(f"[SQL 执行] 返回 {len(rows)} 行")
                break
            except Exception as e:
                print(f"[SQL 执行] 第 {retry + 1} 次运行时报错，尝试改写")
                sql = self.rewrite_sql(question, sql, str(e))
                if not sql:
                    break

        if not validated or not sql:
            print("[SQL Agent] 生成失败，无法得到可执行的 SQL")
            return {"question": question, "tables": tables, "sql": sql, "validated": False, "answer": ""}

        # 7. 结果汇总
        answer = self.summarize(question, sql, columns, rows)

        return {
            "question": question,
            "tables": tables,
            "sql": sql,
            "validated": validated,
            "columns": columns,
            "rows": rows,
            "answer": answer,
        }


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SQL Agent 完整工作流（LangChain 版，基于 chinook.db）")
    parser.add_argument("question", nargs="?", default="每个客户的消费总额是多少？", help="自然语言提问")
    parser.add_argument("--db", default=f"sqlite:///{DEFAULT_DB_PATH}", help="数据库连接地址")
    args = parser.parse_args()

    agent = SQLAgent(args.db)
    result = agent.action(args.question)

    print("\n" + "=" * 60)
    print("最终回答:")
    print("=" * 60)
    print(result["answer"] or "(未能生成回答)")
    print("\n执行 SQL:")
    print(result["sql"])
    print("\n表选择:", result["tables"])


if __name__ == "__main__":
    main()
