"""SQL Agent 工作流。

完整流程：
    数据库解析 -> table 选择 -> SQL 生成 -> SQL 校验 -> SQL 改写 -> SQL 执行 -> 结果汇总
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_parser import DBParser, normalize_sql
from llm import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent / "04_SQL-Code-Agent-Demo" / "chinook.db"
)

# ---------------------------------------------------------------- Prompt 模板
TABLE_SELECTION_PROMPT = """你是一个资深的数据库分析师。请根据用户问题，从候选表中选择回答问题所需的表（1~3 张，宁少勿多）。

{#schema#}

用户问题：{#question#}

要求：
1. 只能从候选表中选择表名，表名必须与候选表完全一致；
2. 如果一张表就能回答，不要选择多余的表；
3. 只输出 JSON，不要输出其他内容，格式为：
{{"tables": ["表名1"], "reason": "选择理由"}}
"""

SQL_GENERATION_PROMPT = """你是一个精通 SQLite 的 SQL 专家。请根据用户问题和给定的表结构，写出可直接执行的 SQLite 查询语句。

{#schema#}

用户问题：{#question#}

要求：
1. 只使用上面列出的表和字段，字段名必须与 schema 完全一致；
2. 只输出一条只读 SELECT 查询，可以使用 JOIN、GROUP BY、子查询和 WITH；
3. 涉及多张表时，请使用外键关系进行 JOIN；
4. 结果行数不确定时，请加上 LIMIT 100；
5. 只输出 SQL，不要输出解释，也不要输出 Markdown 代码块。
"""

SQL_REVIEW_PROMPT = """你是 SQL 审核专家。请判断下面这条 SQLite 查询能否正确回答用户问题。

{#schema#}

用户问题：{#question#}

待审核 SQL：
{#sql#}

审核要点：
1. 表名、字段名是否与 schema 一致；
2. JOIN 条件是否正确；
3. 聚合、过滤、排序是否符合用户意图；
4. 是否存在无法执行或结果明显错误的写法。

只输出 JSON，不要输出其他内容，格式为：
{{"valid": true, "reason": "通过/不通过的原因"}}
"""

SQL_REWRITE_PROMPT = """你是 SQL 修复专家。下面的 SQLite 查询没有通过校验或执行失败，请在不改变用户意图的前提下改写修复它。

{#schema#}

用户问题：{#question#}

当前 SQL：
{#sql#}

问题：
{#issues#}

要求：
1. 只针对问题进行修复，保持用户意图不变；
2. 只使用 schema 中存在的表和字段；
3. 只输出一条只读 SELECT 查询；
4. 只输出 SQL，不要输出解释，也不要输出 Markdown 代码块。
"""

SUMMARY_PROMPT = """你是数据分析助手。请根据用户问题、执行的 SQL 和查询结果，用简洁的自然语言回答用户。

用户问题：{#question#}

执行的 SQL：
{#sql#}

查询结果（共 {#row_count#} 行{#truncated#}）：
{#result#}

要求：
1. 直接回答用户问题，重点给出关键数字和结论；
2. 数字保持查询结果中的精度，不要编造数据；
3. 如果结果为空，说明没有符合条件的数据；
4. 使用中文回答，不要输出 SQL。
"""


class SQLAgentError(RuntimeError):
    """SQL Agent 无法完成任务时抛出。"""


@dataclass
class TraceStep:
    """工作流各阶段的执行记录。"""

    stage: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """一次问答的完整结果。"""

    question: str
    tables: list[str]
    sql: str
    attempts: int
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    answer: str
    trace: list[TraceStep] = field(default_factory=list)


class SQLAgent:
    """串联各阶段能力的 SQL Agent。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        llm: LLMClient | None = None,
        max_rewrite_rounds: int = 2,
        max_rows: int = 200,
        semantic_check: bool = True,
    ) -> None:
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        self.llm = llm or LLMClient()
        self.parser = DBParser(self.db_path)
        self.max_rewrite_rounds = max_rewrite_rounds
        self.max_rows = max_rows
        self.semantic_check = semantic_check
        self._trace: list[TraceStep] = []

        logger.info(
            "数据库解析完成：%s，共 %s 张表，%s 条外键关系",
            self.db_path,
            len(self.parser.table_names),
            len(self.parser.foreign_keys),
        )

    # ----------------------------------------------------------- 工作流入口
    def ask(self, question: str) -> AgentResult:
        """执行完整工作流，返回最终答案与过程记录。"""
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")

        self._trace = []
        self._record(
            "数据库解析",
            f"共 {len(self.parser.table_names)} 张表、{len(self.parser.foreign_keys)} 条外键关系",
            {"tables": self.parser.table_names},
        )

        # 1. table 选择
        tables = self.select_tables(question)
        self._record("table 选择", f"选中 {', '.join(tables)}", {"tables": tables})

        # 2. SQL 生成
        current_sql = self.generate_sql(question, tables)
        attempts = 1
        self._record("SQL 生成", current_sql, {"sql": current_sql})

        # 3~6. 校验 -> 改写 -> 执行
        exec_result: dict[str, Any] | None = None
        last_issues = ""
        for round_idx in range(self.max_rewrite_rounds + 1):
            valid, issues = self.validate_sql(current_sql, question, tables)
            self._record(
                "SQL 校验",
                "通过" if valid else f"未通过：{issues}",
                {"valid": valid, "issues": issues, "sql": current_sql},
            )
            if valid:
                try:
                    exec_result = self.parser.execute_sql(current_sql, self.max_rows)
                    self._record(
                        "SQL 执行",
                        f"返回 {exec_result['row_count']} 行"
                        + ("（已截断）" if exec_result["truncated"] else ""),
                        {
                            "columns": exec_result["columns"],
                            "row_count": exec_result["row_count"],
                            "truncated": exec_result["truncated"],
                        },
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - 执行失败进入改写
                    last_issues = f"SQL 执行失败：{_brief_error(exc)}"
            else:
                last_issues = issues

            if round_idx >= self.max_rewrite_rounds:
                raise SQLAgentError(
                    f"SQL 经过 {attempts} 次生成仍未通过校验：{last_issues}"
                )

            current_sql = self.rewrite_sql(question, tables, current_sql, last_issues)
            attempts += 1
            self._record(
                "SQL 改写",
                f"第 {round_idx + 1} 次改写完成",
                {"sql": current_sql, "issues": last_issues},
            )

        if exec_result is None:
            raise SQLAgentError("SQL 执行结果为空")

        # 7. 结果汇总
        answer = self.summarize(question, current_sql, exec_result)
        self._record("结果汇总", answer, {"answer": answer})

        return AgentResult(
            question=question,
            tables=tables,
            sql=current_sql,
            attempts=attempts,
            columns=exec_result["columns"],
            rows=exec_result["rows"],
            row_count=exec_result["row_count"],
            truncated=exec_result["truncated"],
            answer=answer,
            trace=self._trace,
        )

    # ------------------------------------------------------------- 1. table 选择
    def select_tables(self, question: str) -> list[str]:
        """让模型根据问题选择相关表，失败时回退到关键词匹配。"""
        prompt = (
            TABLE_SELECTION_PROMPT.replace("{#schema#}", self.parser.get_schema_overview())
            .replace("{#question#}", question)
        )
        try:
            data = self.llm.chat_json([{"role": "user", "content": prompt}])
            raw_tables = data.get("tables") or []
            if isinstance(raw_tables, str):
                raw_tables = [raw_tables]
            selected = [
                self._normalize_table_name(name)
                for name in raw_tables
                if isinstance(name, str)
            ]
            selected = [
                name for name in selected if name in self.parser.table_names
            ]
            # 去重并限制数量，避免 Prompt 过长
            selected = list(dict.fromkeys(selected))[:3]
            if selected:
                reason = str(data.get("reason", "")).strip()
                logger.info("table 选择：%s（%s）", selected, reason or "无理由")
                return selected
            logger.warning("模型未选出有效表，使用关键词回退：%s", raw_tables)
        except Exception as exc:  # noqa: BLE001 - 选择失败不应中断流程
            logger.warning("table 选择失败，使用关键词回退：%s", exc)

        fallback = self._fallback_tables(question)
        logger.info("关键词回退结果：%s", fallback)
        return fallback

    @staticmethod
    def _normalize_table_name(name: str) -> str:
        """兼容模型输出的反引号、引号和 main.xxx 前缀。"""
        cleaned = name.strip().strip("`\"'")
        if "." in cleaned:
            cleaned = cleaned.split(".")[-1]
        return cleaned

    def _fallback_tables(self, question: str) -> list[str]:
        """基于表名、字段名和样例值做简单的关键词匹配。"""
        lowered = question.lower()
        scores: list[tuple[int, str]] = []
        for table_name in self.parser.table_names:
            score = 0
            if table_name.lower() in lowered:
                score += 5
            for field in self.parser.get_table_fields(table_name):
                if field["name"].lower() in lowered:
                    score += 2
                for example in field.get("examples", [])[:3]:
                    if str(example).lower() in lowered:
                        score += 1
            scores.append((score, table_name))

        scores.sort(reverse=True)
        matched = [name for score, name in scores if score > 0][:3]
        if matched:
            return matched
        # 完全没有匹配时返回全部表，确保下游仍可生成 SQL
        return list(self.parser.table_names)

    # ------------------------------------------------------------- 2. SQL 生成
    def generate_sql(self, question: str, tables: list[str]) -> str:
        """根据选中的表生成 SQL。"""
        prompt = (
            SQL_GENERATION_PROMPT.replace(
                "{#schema#}", self._schema_for(tables)
            ).replace("{#question#}", question)
        )
        return self._request_sql(prompt, stage="SQL 生成")

    def _request_sql(self, prompt: str, stage: str) -> str:
        """调用模型并抽取 SQL，空结果时追加提醒重试。"""
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(1, 3):
            text = self.llm.chat(messages)
            sql = normalize_sql(text)
            if sql:
                logger.info("%s：%s", stage, sql)
                return sql
            messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "请只输出一条可执行的 SQLite SELECT 语句，不要输出其他内容。"},
                ]
            )
            logger.warning("%s 未抽取到 SQL（第 %s 次）", stage, attempt)
        raise SQLAgentError(f"{stage}失败：模型未输出可用的 SQL")

    # ------------------------------------------------------------- 3. SQL 校验
    def validate_sql(
        self, sql: str, question: str, tables: list[str]
    ) -> tuple[bool, str]:
        """先做数据库只读/语法校验，再做语义校验。"""
        ok, message = self.parser.check_sql(sql)
        if not ok:
            return False, f"数据库校验失败：{_brief_error(message)}"

        if not self.semantic_check:
            return True, "ok"

        prompt = (
            SQL_REVIEW_PROMPT.replace("{#schema#}", self._schema_for(tables))
            .replace("{#question#}", question)
            .replace("{#sql#}", sql)
        )
        try:
            review = self.llm.chat_json([{"role": "user", "content": prompt}])
            valid = review.get("valid", True)
            if isinstance(valid, str):
                valid = valid.strip().lower() not in {"false", "no", "0", "invalid"}
            else:
                valid = bool(valid)
            reason = str(review.get("reason", "")).strip()
            if valid is False:
                logger.info("语义校验未通过：%s", reason or "未说明原因")
                return False, f"语义校验未通过：{reason or 'SQL 与问题不匹配'}"
            logger.info("语义校验通过")
        except Exception as exc:  # noqa: BLE001 - 语义校验失败不阻断流程
            logger.warning("语义校验调用失败，跳过：%s", exc)

        return True, "ok"

    # ------------------------------------------------------------- 4. SQL 改写
    def rewrite_sql(
        self, question: str, tables: list[str], sql: str, issues: str
    ) -> str:
        """根据校验/执行错误改写 SQL。"""
        prompt = (
            SQL_REWRITE_PROMPT.replace("{#schema#}", self._schema_for(tables))
            .replace("{#question#}", question)
            .replace("{#sql#}", sql)
            .replace("{#issues#}", issues)
        )
        return self._request_sql(prompt, stage="SQL 改写")

    # ------------------------------------------------------------- 5. SQL 执行
    def execute_sql(self, sql: str) -> dict[str, Any]:
        """执行 SQL，返回列名、数据行、行数及截断标记。"""
        result = self.parser.execute_sql(sql, self.max_rows)
        self._record(
            "SQL 执行",
            f"返回 {result['row_count']} 行" + ("（已截断）" if result["truncated"] else ""),
            {
                "sql": sql,
                "columns": result["columns"],
                "row_count": result["row_count"],
            },
        )
        return result

    # ------------------------------------------------------------- 6. 结果汇总
    def summarize(
        self, question: str, sql: str, exec_result: dict[str, Any]
    ) -> str:
        """把查询结果汇总为自然语言回答。"""
        if exec_result["row_count"] == 0:
            logger.info("结果汇总：查询结果为空")
            return "没有查询到符合条件的数据。"

        prompt = (
            SUMMARY_PROMPT.replace("{#question#}", question)
            .replace("{#sql#}", sql)
            .replace("{#row_count#}", str(exec_result["row_count"]))
            .replace("{#truncated#}", "，已截断" if exec_result["truncated"] else "")
            .replace("{#result#}", _format_rows(exec_result))
        )
        answer = self.llm.chat([{"role": "user", "content": prompt}])
        logger.info("结果汇总完成")
        return answer.strip()

    # ------------------------------------------------------------------ 工具
    def _schema_for(self, tables: list[str]) -> str:
        schema = self.parser.get_schema_text(tables)
        relations = self.parser.get_data_relations_text(tables)
        if relations == "(无外键关系)":
            return schema
        return f"{schema}\n\n### 表间关系\n{relations}"

    def _record(self, stage: str, detail: str, payload: dict[str, Any] | None = None) -> None:
        self._trace.append(TraceStep(stage=stage, detail=detail, payload=payload or {}))

    def close(self) -> None:
        self.parser.close()

    def __enter__(self) -> "SQLAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _format_rows(exec_result: dict[str, Any], max_preview: int = 50) -> str:
    """把查询结果格式化为 Prompt 文本。"""
    columns = exec_result["columns"]
    lines = [" | ".join(columns)]
    for row in exec_result["rows"][:max_preview]:
        lines.append(" | ".join("NULL" if cell is None else str(cell) for cell in row))
    if exec_result["row_count"] > max_preview:
        lines.append(f"...（其余 {exec_result['row_count'] - max_preview} 行省略）")
    return "\n".join(lines)


def _brief_error(error: Any, limit: int = 300) -> str:
    """截取错误信息中最有价值的一行，避免 Prompt 过长。"""
    text = str(error).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    message = lines[-1] if lines else text
    return message[:limit]
