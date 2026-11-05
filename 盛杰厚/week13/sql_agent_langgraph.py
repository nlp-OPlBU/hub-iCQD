#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL Agent（LangGraph 版）—— 用 LangGraph 复刻 ../week12/作业2/sql_agent_langchain.py

原版用 LangChain 的 LCEL 手写编排：函数按顺序调用，校验/改写/执行是一个 for 循环。
本版把同样的 7 个步骤拆成 LangGraph 的节点（Node），用「边 + 条件边」表达流程，
把原来的 for 循环变成图里的一个环（rewrite_sql -> validate_sql -> ...）。

图结构：
    START
      └─> begin               初始化本轮状态（清空上一轮的 SQL / 结果 / 重试计数）
            └─> select_tables  步骤 2：表选择（JsonOutputParser 结构化输出）
                  └─> generate_sql  步骤 3：SQL 生成
                        └─> validate_sql  步骤 4：编译校验（只编译不执行）
                              ├─(编译通过)────────────> execute_sql  步骤 6：执行
                              ├─(编译失败 & 可重试)──> rewrite_sql  步骤 5：改写 ─┐
                              └─(超过重试次数)───────> fail                     │
                                                             ^─────────────────┘
                                                             │（改写后回到校验）
    execute_sql ─(运行时报错 & 可重试)──> rewrite_sql
                ─(成功)──> summarize  步骤 7：结果汇总 ──> remember ──> END
                ─(超过重试次数)──> fail ──────────────────> remember ──> END

与原版的一一对应：
    action() 里的 7 个步骤方法        -> 图里的 7 个节点
    for retry in range(max_retry) 循环 -> rewrite_sql -> validate_sql 这条环 + 条件边
    action() 的返回字典               -> 图的最终 State（字段基本一致）

为「网页多轮对话」新增的能力（原版是单轮 CLI，没有这些）：
    - State 中保存 history：历史问答会作为上下文传给表选择 / SQL 生成 / 汇总，
      因此支持「那前 10 个呢？」这类指代上一轮的问题
    - InMemorySaver 检查点：用 thread_id 区分浏览器会话，同一 thread_id 的多次调用自动串成多轮
    - run_stream()：把每个节点的执行进度实时抛给前端（SSE）

依赖: langgraph, langchain-core, langchain-openai, sqlalchemy>=2.0, pandas
用法:
    export DEEPSEEK_API_KEY=你的key
    python sql_agent_langgraph.py "每个客户的消费总额是多少？"
    DEEPSEEK_API_KEY=xxx DEEPSEEK_MODEL=deepseek-flash python sql_agent_langgraph.py "每个流派有多少首歌？"
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

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

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
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

# 多轮对话保留的历史条数（user + assistant 各算一条）
MAX_HISTORY_MESSAGES = 20


# ---------------------------------------------------------------------------
# 步骤 1：数据库解析（纯 SQLAlchemy，与 LLM 无关，与原版完全一致）
# ---------------------------------------------------------------------------
class DBParser:
    """解析数据库：表名、字段、主外键、统计信息与数据样例。

    db_url 支持：
        sqlite: sqlite:///./chinook.db
        mysql:  mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
    """

    def __init__(self, db_url: str, verbose: bool = True) -> None:
        self.db_url = db_url
        self.verbose = verbose
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
                if self.verbose:
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

    def schema_summary(self) -> List[Dict[str, Any]]:
        """给前端「数据表」面板用的精简结构：表名 + 字段列表"""
        summary = []
        for table_name in self.table_names:
            summary.append(
                {
                    "name": table_name,
                    "columns": [
                        {
                            "name": name,
                            "type": info["type"],
                            "primary_key": info["primary_key"],
                        }
                        for name, info in self._table_fields[table_name].items()
                    ],
                }
            )
        return summary

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
            import traceback

            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> Tuple[List[str], List[Any]]:
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
# LangGraph 的共享状态（State）
# 节点之间通过这些字段传递数据，等价于原版 action() 里的一串局部变量。
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    # ---- 输入 ----
    question: str          # 用户本轮提问
    verbose: bool          # 是否打印过程日志（CLI 为 True，网页为 False）

    # ---- 多轮对话历史（由 remember 节点维护，跨轮次保留） ----
    history: List[Dict[str, str]]

    # ---- 中间结果 ----
    tables: List[str]      # 步骤 2 选中的表
    sql: str               # 当前 SQL（生成/改写后的版本）
    compile_ok: bool       # 步骤 4 编译校验是否通过
    error: str             # 最近一次校验/执行的报错
    retries: int           # 已改写次数

    # ---- 执行结果 ----
    validated: bool        # 是否成功执行（与原版返回值的 validated 同义）
    columns: List[str]
    rows: List[Any]
    failed: bool           # 流程失败（没拿到可执行 SQL）

    # ---- 最终回答 ----
    answer: str

    # ---- 执行轨迹（每个节点一条，供命令行打印和前端时间线展示） ----
    steps: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# SQL Agent（LangGraph 版）
# ---------------------------------------------------------------------------
class SQLAgent:
    """把表选择 -> SQL 生成 -> 校验 -> 改写 -> 执行 -> 汇总 编排成一张 LangGraph 图。"""

    def __init__(
        self,
        db_url: str,
        api_key: str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEFAULT_MODEL,
        max_retry: int = 5,
        llm: Optional[Any] = None,
        summary_llm: Optional[Any] = None,
        verbose: bool = True,
        checkpointer: Optional[Any] = None,
    ) -> None:
        self.parser = DBParser(db_url, verbose=verbose)
        self.model = model
        self.max_retry = max_retry

        # 允许外部注入 LLM（无 Key 的调试模式 / 单元测试用），否则走 OpenAI 兼容接口。
        if llm is None:
            if not api_key:
                raise ValueError(
                    "缺少 API Key：请先设置环境变量 DEEPSEEK_API_KEY（或传入 api_key 参数）。"
                )
            # LangChain 的 ChatOpenAI：OpenAI 兼容接口，默认指向 DeepSeek。
            # 推理步骤（表选择/SQL 生成/SQL 改写）用低温度，汇总用稍高温度更自然。
            llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, temperature=0.1)
            summary_llm = summary_llm or ChatOpenAI(
                api_key=api_key, base_url=base_url, model=model, temperature=0.6
            )
        self.llm = llm
        self.summary_llm = summary_llm or llm

        # ------------------------------------------------------------------
        # 各步骤的 LCEL 链（prompt -> llm -> parser），与原版一致，
        # 在 LangGraph 里它们被包进对应节点执行。
        # ------------------------------------------------------------------
        # 步骤 2：表选择 —— 结构化输出（JsonOutputParser + Pydantic）
        self._table_select_parser = JsonOutputParser(pydantic_object=TableSelection)
        self._table_select_prompt = PromptTemplate(
            template=(
                "你是一个数据库专家。请根据用户提问，从下面的数据库中选择回答该问题所需的数据表"
                "（可以多选，也可以只选一张）。\n\n"
                "数据库中的所有表名：{table_names}\n\n"
                "表之间的外键关系：\n{relations}\n\n"
                "每张表的字段结构：\n{schema}\n\n"
                "对话历史（用于理解“那…呢”这类指代，没有则为“(无)”）：\n{history}\n\n"
                "用户提问：{question}\n\n"
                "{format_instructions}"
            ),
            input_variables=["table_names", "relations", "schema", "history", "question"],
            partial_variables={"format_instructions": self._table_select_parser.get_format_instructions()},
        )
        self._table_select_chain = self._table_select_prompt | self.llm | self._table_select_parser

        # 步骤 3：SQL 生成
        self._sql_gen_prompt = PromptTemplate(
            template=(
                "你是一个数据库专家。请根据用户提问和表结构，生成一条能在 SQLite 上直接执行的 SQL 语句。\n\n"
                "对话历史（用于理解“那…呢”这类指代，没有则为“(无)”）：\n{history}\n\n"
                "用户提问：{question}\n\n"
                "可用的表及其字段结构：\n{schema}\n\n"
                "表之间的外键关系：\n{relations}\n\n"
                "请直接输出 SQL 语句（不要用 markdown 代码块，不要输出任何解释）。"
            ),
            input_variables=["question", "history", "schema", "relations"],
        )
        self._sql_gen_chain = self._sql_gen_prompt | self.llm | StrOutputParser()

        # 步骤 5：SQL 改写
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

        # 步骤 7：结果汇总
        self._summary_prompt = PromptTemplate(
            template=(
                "你是答案汇总专家。请把用户提问、执行的 SQL 和执行结果组织为自然语言回答。\n\n"
                "对话历史（用于理解“那…呢”这类指代，没有则为“(无)”）：\n{history}\n\n"
                "用户提问：{question}\n\n"
                "执行 SQL：{sql}\n\n"
                "执行结果：\n{result}\n\n"
                "请直接输出自然语言回答。"
            ),
            input_variables=["question", "history", "sql", "result"],
        )
        self._summary_chain = self._summary_prompt | self.summary_llm | StrOutputParser()

        # 多轮对话的记忆：InMemorySaver 按 thread_id 保存每个会话的 State
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.graph = self._build_graph()

    # ==================================================================
    # 通用小工具
    # ==================================================================
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

    @staticmethod
    def _format_history(history: Optional[List[Dict[str, str]]], max_turns: int = 6) -> str:
        """把历史问答拼进提示词；只取最近 max_turns 轮，避免提示词过长。"""
        if not history:
            return "(无)"
        lines = []
        for item in history[-(max_turns * 2):]:
            role = "用户" if item.get("role") == "user" else "助手"
            content = str(item.get("content", "")).strip()
            if len(content) > 300:
                content = content[:300] + "…"
            lines.append(f"{role}：{content}")
        return "\n".join(lines)

    @staticmethod
    def _step(
        state: AgentState,
        node: str,
        title: str,
        status: str,
        detail: str = "",
        elapsed_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """在 steps 轨迹里追加一条记录，返回新的完整列表（State 里是覆盖写）。"""
        steps = list(state.get("steps") or [])
        steps.append(
            {
                "node": node,
                "title": title,
                "status": status,          # ok / retry / error
                "detail": detail,
                "elapsed_ms": elapsed_ms,
            }
        )
        return steps

    # ==================================================================
    # 节点实现（每个节点返回 State 的部分更新）
    # ==================================================================
    def _node_begin(self, state: AgentState) -> Dict[str, Any]:
        """初始化本轮状态：清空上一轮的 SQL / 结果 / 重试计数（history 保留）。"""
        if state.get("verbose", True):
            print("=" * 60)
            print(f"提问: {state['question']}")
            print("=" * 60)
        return {
            "tables": [],
            "sql": "",
            "compile_ok": False,
            "error": "",
            "retries": 0,
            "validated": False,
            "columns": [],
            "rows": [],
            "failed": False,
            "answer": "",
            "steps": [],
        }

    def _node_select_tables(self, state: AgentState) -> Dict[str, Any]:
        """步骤 2：表选择。"""
        started = time.time()
        question = state["question"]
        history_text = self._format_history(state.get("history"))
        table_names = ", ".join(self.parser.table_names)
        relations = self.parser.describe_relations()
        # 表选择阶段只给字段结构（不含样例），信息足够且更省 token
        schema = self.parser.describe_schema(self.parser.table_names, include_samples=False)

        result = self._invoke_chain(
            self._table_select_chain,
            {
                "table_names": table_names,
                "relations": relations,
                "schema": schema,
                "history": history_text,
                "question": question,
            },
        )
        if state.get("verbose", True):
            print(f"[表选择] 原始输出: {result}")

        # 从结构化结果里取表名，只保留真实存在的表，非法输出回退为全部表
        tables = result.get("tables", []) if isinstance(result, dict) else []
        selected = []
        for name in tables:
            if isinstance(name, str) and name in self.parser.table_names and name not in selected:
                selected.append(name)
        if not selected:
            if state.get("verbose", True):
                print("[表选择] 未解析出有效表名，回退为全部表")
            selected = list(self.parser.table_names)

        elapsed = int((time.time() - started) * 1000)
        return {
            "tables": selected,
            "steps": self._step(state, "select_tables", "表选择", "ok", "、".join(selected), elapsed),
        }

    def _node_generate_sql(self, state: AgentState) -> Dict[str, Any]:
        """步骤 3：SQL 生成。"""
        started = time.time()
        tables = state.get("tables") or list(self.parser.table_names)
        schema = self.parser.describe_schema(tables, include_samples=True)
        relations = self.parser.describe_relations()
        raw = self._invoke_chain(
            self._sql_gen_chain,
            {
                "question": state["question"],
                "history": self._format_history(state.get("history")),
                "schema": schema,
                "relations": relations,
            },
        )
        sql = self._extract_sql(raw)
        if state.get("verbose", True):
            print(f"[SQL 生成]\n{sql}")

        if not sql:
            elapsed = int((time.time() - started) * 1000)
            return {
                "sql": "",
                "steps": self._step(state, "generate_sql", "SQL 生成", "error", "模型没有返回 SQL", elapsed),
            }

        elapsed = int((time.time() - started) * 1000)
        return {
            "sql": sql,
            "steps": self._step(state, "generate_sql", "SQL 生成", "ok", sql, elapsed),
        }

    def _node_validate_sql(self, state: AgentState) -> Dict[str, Any]:
        """步骤 4：SQL 编译校验（只编译、不执行）。"""
        started = time.time()
        sql = state.get("sql") or ""
        ok, err = self.parser.compile_check(sql)
        elapsed = int((time.time() - started) * 1000)
        if ok:
            return {
                "compile_ok": True,
                "error": "",
                "steps": self._step(state, "validate_sql", "SQL 校验", "ok", "编译通过", elapsed),
            }
        # 只保留报错的最后几行，方便前端展示
        brief = err.strip().splitlines()[-1] if err.strip() else "编译失败"
        return {
            "compile_ok": False,
            "error": err,
            "steps": self._step(state, "validate_sql", "SQL 校验", "retry", brief, elapsed),
        }

    def _node_rewrite_sql(self, state: AgentState) -> Dict[str, Any]:
        """步骤 5：SQL 改写（结合报错信息让模型修正）。"""
        started = time.time()
        schema = self.parser.describe_schema(self.parser.table_names, include_samples=True)
        raw = self._invoke_chain(
            self._sql_rewrite_chain,
            {
                "question": state["question"],
                "schema": schema,
                "sql": state.get("sql") or "",
                "error": state.get("error") or "",
            },
        )
        sql = self._extract_sql(raw)
        if state.get("verbose", True):
            print(f"[SQL 改写]\n{sql}")

        elapsed = int((time.time() - started) * 1000)
        return {
            "sql": sql,
            "error": "",
            "retries": int(state.get("retries") or 0) + 1,
            "steps": self._step(
                state,
                "rewrite_sql",
                "SQL 改写",
                "ok" if sql else "error",
                sql or "改写失败",
                elapsed,
            ),
        }

    def _node_execute_sql(self, state: AgentState) -> Dict[str, Any]:
        """步骤 6：SQL 执行（运行时报错也回退到改写）。"""
        started = time.time()
        try:
            columns, rows = self.parser.execute_sql(state.get("sql") or "")
        except Exception as e:  # 运行时错误
            elapsed = int((time.time() - started) * 1000)
            return {
                "error": str(e),
                "validated": False,
                "steps": self._step(state, "execute_sql", "SQL 执行", "retry", str(e)[:200], elapsed),
            }

        if state.get("verbose", True):
            print(f"[SQL 执行] 返回 {len(rows)} 行")
        elapsed = int((time.time() - started) * 1000)
        return {
            "columns": columns,
            "rows": rows,
            "error": "",
            "validated": True,
            "steps": self._step(state, "execute_sql", "SQL 执行", "ok", f"{len(rows)} 行", elapsed),
        }

    def _node_summarize(self, state: AgentState) -> Dict[str, Any]:
        """步骤 7：结果汇总（把 SQL 结果组织成自然语言）。"""
        started = time.time()
        columns = state.get("columns") or []
        rows = state.get("rows") or []
        result = self._format_result(columns, rows)
        answer = self._invoke_chain(
            self._summary_chain,
            {
                "question": state["question"],
                "history": self._format_history(state.get("history")),
                "sql": state.get("sql") or "",
                "result": result,
            },
        )
        answer = (answer or "").strip()
        if not answer:
            # 汇总模型失败时，至少把结果本身回给用户
            answer = f"查询成功，共 {len(rows)} 行结果。\n\n{result}"

        elapsed = int((time.time() - started) * 1000)
        return {
            "answer": answer,
            "steps": self._step(state, "summarize", "结果汇总", "ok", "", elapsed),
        }

    def _node_fail(self, state: AgentState) -> Dict[str, Any]:
        """多次校验/改写仍失败：标记失败并给出提示。"""
        reason = ""
        if state.get("error"):
            reason = str(state["error"]).strip().splitlines()[-1]
        message = "抱歉，经过多次校验与改写仍然没有得到可执行的 SQL。"
        if reason:
            message += f"\n\n最后的错误信息：{reason}"
        return {
            "failed": True,
            "answer": message,
            "steps": self._step(state, "fail", "生成失败", "error", reason, None),
        }

    def _node_remember(self, state: AgentState) -> Dict[str, Any]:
        """把本轮问答写入 history，供下一轮对话使用。"""
        history = list(state.get("history") or [])
        history.append({"role": "user", "content": state.get("question") or ""})
        answer = (state.get("answer") or "").strip()
        if answer:
            history.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "sql": state.get("sql") or "",
                    "tables": state.get("tables") or [],
                }
            )
        return {"history": history[-MAX_HISTORY_MESSAGES:]}

    # ==================================================================
    # 条件边：决定下一步走哪个节点
    # ==================================================================
    def _route_after_generate(self, state: AgentState) -> str:
        """没生成出 SQL 时直接失败，不再空转重试。"""
        return "validate" if (state.get("sql") or "").strip() else "fail"

    def _route_after_validate(self, state: AgentState) -> str:
        """编译通过 -> 执行；编译失败 -> 改写（不超过 max_retry 次）。"""
        if state.get("compile_ok"):
            return "execute"
        if int(state.get("retries") or 0) >= self.max_retry:
            return "fail"
        if not (state.get("sql") or "").strip():
            return "fail"
        return "rewrite"

    def _route_after_execute(self, state: AgentState) -> str:
        """执行成功 -> 汇总；运行时报错 -> 改写（不超过 max_retry 次）。"""
        if state.get("validated"):
            return "summary"
        if int(state.get("retries") or 0) >= self.max_retry:
            return "fail"
        return "rewrite"

    # ==================================================================
    # 建图
    # ==================================================================
    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("begin", self._node_begin)
        builder.add_node("select_tables", self._node_select_tables)
        builder.add_node("generate_sql", self._node_generate_sql)
        builder.add_node("validate_sql", self._node_validate_sql)
        builder.add_node("rewrite_sql", self._node_rewrite_sql)
        builder.add_node("execute_sql", self._node_execute_sql)
        builder.add_node("summarize", self._node_summarize)
        builder.add_node("fail", self._node_fail)
        builder.add_node("remember", self._node_remember)

        builder.add_edge(START, "begin")
        builder.add_edge("begin", "select_tables")
        builder.add_edge("select_tables", "generate_sql")

        builder.add_conditional_edges(
            "generate_sql",
            self._route_after_generate,
            {"validate": "validate_sql", "fail": "fail"},
        )
        # 原版 for 循环里的「校验 -> 改写 -> 再校验」在这里变成 rewrite_sql -> validate_sql 的环
        builder.add_conditional_edges(
            "validate_sql",
            self._route_after_validate,
            {"execute": "execute_sql", "rewrite": "rewrite_sql", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "execute_sql",
            self._route_after_execute,
            {"summary": "summarize", "rewrite": "rewrite_sql", "fail": "fail"},
        )
        builder.add_edge("rewrite_sql", "validate_sql")

        builder.add_edge("summarize", "remember")
        builder.add_edge("fail", "remember")
        builder.add_edge("remember", END)

        return builder.compile(checkpointer=self.checkpointer)

    # ==================================================================
    # 对外接口
    # ==================================================================
    @staticmethod
    def _format_result(columns: List[str], rows: List[Any], max_rows: int = 200) -> str:
        if not rows:
            return "(无结果)"
        header = " | ".join(columns)
        body = [" | ".join("" if v is None else str(v) for v in row) for row in rows[:max_rows]]
        out = [header] + body
        if len(rows) > max_rows:
            out.append(f"... (共 {len(rows)} 行，仅展示前 {max_rows} 行)")
        return "\n".join(out)

    def action(self, question: str, verbose: bool = True, thread_id: str = "cli") -> Dict[str, Any]:
        """执行一轮问答（对应原版的 action()），返回最终 State 的精简版。"""
        started = time.time()
        config = {"configurable": {"thread_id": thread_id}}
        final = self.graph.invoke({"question": question, "verbose": verbose}, config)
        result = {
            "question": question,
            "tables": final.get("tables") or [],
            "sql": final.get("sql") or "",
            "validated": bool(final.get("validated")),
            "failed": bool(final.get("failed")),
            "columns": final.get("columns") or [],
            "rows": final.get("rows") or [],
            "answer": final.get("answer") or "",
            "retries": int(final.get("retries") or 0),
            "steps": final.get("steps") or [],
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        return result

    def run_stream(self, question: str, thread_id: str = "cli", verbose: bool = False):
        """按节点流式执行，逐个 yield (节点名, 该节点的 State 更新)，供网页显示进度。"""
        config = {"configurable": {"thread_id": thread_id}}
        for update in self.graph.stream(
            {"question": question, "verbose": verbose}, config, stream_mode="updates"
        ):
            for node_name, node_state in update.items():
                yield node_name, node_state or {}

    def get_state(self, thread_id: str) -> Dict[str, Any]:
        """读取某个会话的完整 State（多轮对话的 history 也在这里）。"""
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = self.graph.get_state(config)
        return dict(snapshot.values or {})

    def reset_thread(self, thread_id: str) -> None:
        """清空某个会话的记忆。"""
        delete = getattr(self.checkpointer, "delete_thread", None)
        if callable(delete):
            delete(thread_id)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SQL Agent 完整工作流（LangGraph 版，基于 chinook.db）")
    parser.add_argument("question", nargs="?", default="每个客户的消费总额是多少？", help="自然语言提问")
    parser.add_argument("--db", default=f"sqlite:///{DEFAULT_DB_PATH}", help="数据库连接地址")
    parser.add_argument("--thread", default="cli", help="会话 ID（同一个 ID 的多次调用会共享对话历史）")
    args = parser.parse_args()

    agent = SQLAgent(args.db)
    result = agent.action(args.question, thread_id=args.thread)

    print("\n" + "=" * 60)
    print("最终回答:")
    print("=" * 60)
    print(result["answer"] or "(未能生成回答)")
    print("\n执行 SQL:")
    print(result["sql"])
    print("\n表选择:", result["tables"])


if __name__ == "__main__":
    main()
