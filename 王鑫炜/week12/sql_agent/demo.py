"""离线工具调用回放：实际运行 LangChain graph 和 SQLite，不调用真实模型。"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "offline-scripted-test-model"

    def bind_tools(self, tools, **kwargs: Any):
        # LangChain create_agent 使用同一个接口绑定真实工具；此处仅替换模型回复。
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._cursor >= len(self.responses):
            raise ValueError("离线回放响应已耗尽")
        response = self.responses[self._cursor]
        self._cursor += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def call(name, args, number):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"demo-{number}", "type": "tool_call"}])


def make_demo(name):
    cases = {
        "count": ("有专辑的艺术家共有多少位？", ["albums"],
                  "SELECT COUNT(DISTINCT ArtistId) AS artist_count FROM albums",
                  "有专辑的艺术家共有 204 位，按 albums.ArtistId 去重统计。"),
        "join": ("购买曲目数量最多的前五个音乐流派是什么？", ["genres", "invoice_items"],
                 "SELECT g.Name AS genre, SUM(ii.Quantity) AS quantity FROM genres AS g "
                 "JOIN tracks AS t ON t.GenreId = g.GenreId "
                 "JOIN invoice_items AS ii ON ii.TrackId = t.TrackId "
                 "GROUP BY g.GenreId, g.Name ORDER BY quantity DESC, g.GenreId ASC LIMIT 5",
                 "已按发票明细的 Quantity 汇总销量，并通过 tracks 关联流派。具体前五名及数量见 queries 中的真实结果。"),
        "rewrite": ("商店历史发票总销售额是多少？", ["invoices"],
                    "SELECT ROUND(SUM(Total), 2) AS revenue FROM invoices",
                    "历史发票总销售额为 2328.60；按 invoices.Total 汇总，数据库未注明币种。"),
    }
    question, tables, sql, answer = cases[name]
    replies = [call("list_tables", {}, 1), call("describe_tables", {"table_names": tables}, 2)]
    if name == "rewrite":
        replies.append(call("check_sql", {"sql": "SELECT SUM(Amount) FROM invoices"}, 3))
    replies.extend([call("check_sql", {"sql": sql}, 4), call("query_sql", {"sql": sql}, 5), AIMessage(content=answer)])
    return question, ScriptedChatModel(responses=replies)
