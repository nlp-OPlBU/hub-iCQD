from langchain_openai import ChatOpenAI

from typing import Protocol, TypeVar, Any
from langchain_core.runnables import Runnable
import json

from pydantic import BaseModel

from .config import Settings

from .models import (
    TableSelection,
    SqlDraft,
    QueryResult,
    FinalAnswer,
)

from langchain_core.prompts import ChatPromptTemplate

PROMPT_VERSION = "2026-10-28.v1"
OutputT = TypeVar("OutputT", bound=BaseModel)

class ModelContractError(RuntimeError):
    pass

class ModelGateway(Protocol):
    """隔离 LangChain 模型，便于工作流使用假实现进行无费用测试。"""
    async def select_tables(
        self, question: str, schema_context: str, request_id: str
    ) -> TableSelection: ...

    async def generate_sql(
        self, question: str, focused_schema: str, request_id: str
    ) -> SqlDraft: ...

    async def rewrite_sql(
        self,
        question: str,
        focused_schema: str,
        sql: str,
        error: str,
        history: list[str],
        request_id: str,
    ) -> SqlDraft: ...

    async def synthesize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        request_id: str,
    ) -> FinalAnswer: ...

class LangChainModelGateway:
    """用 LangChain 模型适配 ModelGateway。"""

    def __init__(self, settings : Settings) -> None:
        is_deepseek = settings.provider == "deepseek"
        self.model = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0.0,
            timeout=settings.timeout,
            max_retries=settings.max_retries,
            # DeepSeek 思考模式不支持指定 tool_choice，且会忽略 temperature。
            extra_body=(
                {"thinking": {"type": "disabled"}} if is_deepseek else None
            ),
        )
        # DeepSeek 不支持 response_format=json_schema，只能走工具调用实现结构化输出。
        method = "function_calling" if is_deepseek else "json_schema"

        def structured(schema: type[BaseModel]) -> Runnable:
            return self.model.with_structured_output(schema, method=method)

        self.table_selector = (
            ChatPromptTemplate.from_messages([
                (
                    "system",
                    "你为 SQLite 查询选择最少但足够的表。只能使用输入目录中的精确表名，"
                    "结合字段和外键判断关联路径，最多选择 6 张表。",
                ),
                (
                    "human",
                    "用户问题：{question}\n\n数据库目录：\n{schema_context}",
                ),
            ])
            | structured(TableSelection)
        )

        self.sql_generator = (
            ChatPromptTemplate.from_messages([
                (
                    "system",
                    "你把中文问题转换为一条 SQLite SQL。不要输出 Markdown。查询只能是 SELECT "
                    "或只读 WITH；字段必须来自给定 Schema；排名必须有确定性 ORDER BY；精确名称"
                    "使用精确匹配；正确处理 NULL、日期和金额。若问题明确要求写入或建表，忠实输出"
                    "其意图对应的 SQL，由下游安全校验拒绝，不要悄悄改成查询。",
                ),
                (
                    "human",
                    "用户问题：{question}\n\n可用 Schema 与脱敏样本：\n{focused_schema}",
                ),
            ])
            | structured(SqlDraft)
        )

        self.sql_rewriter = (
            ChatPromptTemplate.from_messages([
                (
                    "system",
                    "只修复给定 SQLite SQL 的语法或 Schema 错误，不改变问题含义，不删除关键筛选"
                    "条件，不输出 Markdown，只返回一条只读 SELECT 或 WITH 查询。"
                ),
                (
                    "human",
                    "用户问题：{question}\n当前 SQL：{sql}\nSQLite 错误：{error}\n"
                    "历史失败 SQL：{history}\n"
                    "可用 Schema：\n{focused_schema}"
                )
            ])
            | structured(SqlDraft)
        )
        
        self.answer_synthesizer = (
            ChatPromptTemplate.from_messages([
                (
                    "system",
                    "用中文直接回答问题。查询结果是唯一事实来源，不补充常识或猜测；专有名词和数字"
                    "不得改写；金额保留两位小数并注明美元；空结果明确回答没有或 0；除非用户要求，"
                    "不要展示 SQL；结果截断时必须说明。"
                ),
                (
                    "human",
                    "用户问题：{question}\n最终 SQL：{sql}\n查询结果：{result}",
                )
            ])
            | structured(FinalAnswer)
        )
        
    
    async def select_tables(
        self, question: str, schema_context: str, request_id: str
    ) -> TableSelection:
        return await self._run(
            self.table_selector,
            {"question": question, "schema_context": schema_context},
            request_id,
            "select_tables",
            TableSelection,
        )

    async def generate_sql(
        self, question: str, focused_schema: str, request_id: str
    ) -> SqlDraft:
        return await self._run(
            self.sql_generator,
            {"question": question, "focused_schema": focused_schema},
            request_id,
            "generate_sql",
            SqlDraft,
        )
    
    async def rewrite_sql(
        self,
        question: str,
        focused_schema: str,
        sql: str,
        error: str,
        history: list[str],
        request_id: str,
    ) -> SqlDraft:
        return await self._run(
            self.sql_rewriter,
            {
                "question": question, 
                "focused_schema": focused_schema, 
                "sql": sql, 
                "error": error, 
                "history": json.dumps(history, ensure_ascii=False)
            },
            request_id,
            "rewrite_sql",
            SqlDraft,
        )

    async def synthesize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        request_id: str,
    ) -> FinalAnswer:
        return await self._run(
            self.answer_synthesizer,
            {"question": question, "sql": sql, "result": result},
            request_id,
            "synthesize",
            FinalAnswer,
        )

    async def _run(
        self,
        chain: Runnable,
        inputs: dict[str, Any],
        request_id: str,
        run_name: str,
        expected_type: type[OutputT],
    ) -> OutputT:
        result = await chain.ainvoke(
            inputs,
            config={
                "run_name": run_name,
                "tags": [run_name],
                "metadata": {
                    "request_id": request_id,
                    "prompt_version": PROMPT_VERSION,
                },
            },
        )
        if not isinstance(result, expected_type):
            raise ModelContractError(
                f"模型输出类型错误，期望 {expected_type.__name__}"
            )
        return result