from langchain_openai import ChatOpenAI

from collections.abc import AsyncIterator
from typing import Protocol, TypeVar, Any
from langchain_core.runnables import Runnable
import json

from pydantic import BaseModel
from langchain_core.output_parsers import StrOutputParser

from .config import Settings

from .models import (
    ConversationTurn,
    TableSelection,
    SqlDraft,
    QueryResult,
    FinalAnswer,
    StandaloneQuestion,
)

from langchain_core.prompts import ChatPromptTemplate

PROMPT_VERSION = "2026-11-05.v3"
OutputT = TypeVar("OutputT", bound=BaseModel)

class ModelContractError(RuntimeError):
    pass

class ModelGateway(Protocol):
    """隔离 LangChain 模型，便于工作流使用假实现进行无费用测试。"""
    async def contextualize(
        self,
        question: str,
        history: list[ConversationTurn],
        request_id: str,
    ) -> StandaloneQuestion: ...

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


    def stream_synthesize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        request_id: str,
    ) -> AsyncIterator[str]: ...


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

        self.question_contextualizer = (
            ChatPromptTemplate.from_messages([
                (
                    "system",
                    "把当前问题改写成无需对话上下文也能理解的独立数据查询问题。"
                    "仅使用历史中明确出现的条件，不猜测；保留当前问题的新要求。"
                    "若当前问题已完整或与历史无关，原样返回。不要生成 SQL。",
                ),
                (
                    "human",
                    "最近对话（按时间顺序）：{history}\n\n当前问题：{question}",
                ),
            ])
            | structured(StandaloneQuestion)
        )

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
                    "使用精确匹配；正确处理 NULL、日期和金额；聚合或计算字段使用简洁、清晰的中文"
                    "别名。若问题明确要求写入或建表，忠实输出其意图对应的 SQL，由下游安全校验"
                    "拒绝，不要悄悄改成查询。",
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
                    "不得改写；金额保留两位小数并注明美元；空结果明确回答没有或 0。回答只做分析："
                    "先给结论，再指出最重要的比较或趋势，总计 1 到 3 句话。禁止输出 Markdown 表格、"
                    "列表或逐行复述查询结果；即使用户要求列出全部记录，也只概括关键项，完整数据由"
                    "界面的结果区域展示。不要展示 SQL。仅在结果确实被截断时说明，不要主动声明"
                    "“未截断”。"
                ),
                (
                    "human",
                    "用户问题：{question}\n最终 SQL：{sql}\n查询结果：{result}",
                )
            ])
            | self.model
            | StrOutputParser()
        )
        
    
    async def contextualize(
        self,
        question: str,
        history: list[ConversationTurn],
        request_id: str,
    ) -> StandaloneQuestion:
        serialized = json.dumps(
            [turn.model_dump(mode="json") for turn in history],
            ensure_ascii=False,
        )
        return await self._run(
            self.question_contextualizer,
            {"question": question, "history": serialized},
            request_id,
            "contextualize",
            StandaloneQuestion,
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
        chunks = [
            chunk
            async for chunk in self.stream_synthesize(
                question, sql, result, request_id
            )
        ]
        answer = "".join(chunks).strip()
        if not answer:
            raise ModelContractError("模型返回了空答案")
        return FinalAnswer(answer=answer)

    async def stream_synthesize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        request_id: str,
    ) -> AsyncIterator[str]:
        config = self._trace_config(request_id, "synthesize")
        async for chunk in self.answer_synthesizer.astream(
            {"question": question, "sql": sql, "result": result},
            config=config,
        ):
            if not isinstance(chunk, str):
                raise ModelContractError("模型答案流包含非文本内容")
            if chunk:
                yield chunk

    async def _run(
        self,
        chain: Runnable,
        inputs: dict[str, Any],
        request_id: str,
        run_name: str,
        expected_type: type[OutputT],
    ) -> OutputT:
        result = await chain.ainvoke(
            inputs, config=self._trace_config(request_id, run_name)
        )
        if not isinstance(result, expected_type):
            raise ModelContractError(
                f"模型输出类型错误，期望 {expected_type.__name__}"
            )
        return result

    @staticmethod
    def _trace_config(request_id: str, run_name: str) -> dict[str, object]:
        return {
            "run_name": run_name,
            "tags": [run_name],
            "metadata": {
                "request_id": request_id,
                "prompt_version": PROMPT_VERSION,
            },
        }