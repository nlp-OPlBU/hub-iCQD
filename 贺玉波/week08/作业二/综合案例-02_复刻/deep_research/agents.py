from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from .models import (
    ExtractedNote, GapDecision, Report, ResearchPlan, SearchResult, SubQuestion,
)

# 提示词与代码分离：模板位于 deep_research/templates/，每个角色一个文件。
# 注意：MockLLM 按关键词分发（规划→补检→抽取→综合），模板中的关键词不可删除。
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")))


def _load(template_name: str) -> str:
    return _env.get_template(template_name).render()


PLANNER_SYSTEM = _load("planner_agent.jinja2")
READER_SYSTEM = _load("reader_agent.jinja2")
REFINER_SYSTEM = _load("refiner_agent.jinja2")
SYNTHESIZER_SYSTEM = _load("synthesizer_agent.jinja2")


class AgentError(Exception):
    """Agent 阶段输出不符合预期结构。"""


class AgentRunner:
    def __init__(self, llm):
        self.llm = llm

    def plan(self, topic: str) -> ResearchPlan:
        data = self.llm.chat_json(PLANNER_SYSTEM, f"研究主题：{topic}")
        try:
            return ResearchPlan.model_validate(data)
        except ValidationError as e:
            raise AgentError(f"规划输出解析失败: {e}") from e

    def read(self, sub_question: SubQuestion,
             results: list[SearchResult]) -> list[ExtractedNote]:
        results_text = "\n".join(
            f"- 标题：{r.title}\n  来源：{r.url}\n  摘要：{r.snippet}" for r in results)
        data = self.llm.chat_json(
            READER_SYSTEM,
            f"子问题：{sub_question.question}\n\n搜索结果：\n{results_text or '(无结果)'}")
        try:
            # 校验前注入 sub_question_id，保证来源归属可追溯
            notes = [ExtractedNote.model_validate(
                {**item, "sub_question_id": sub_question.id}) for item in data]
        except (ValidationError, TypeError) as e:
            raise AgentError(f"阅读输出解析失败: {e}") from e
        return notes

    def refine(self, sub_question: SubQuestion,
               notes: list[ExtractedNote]) -> GapDecision:
        notes_text = "\n".join(f"- {n.text}（{n.source_url}）" for n in notes)
        data = self.llm.chat_json(
            REFINER_SYSTEM,
            f"子问题：{sub_question.question}\n\n已抽取要点：\n{notes_text or '(无)'}")
        try:
            return GapDecision.model_validate(data)
        except ValidationError as e:
            raise AgentError(f"补检判断输出解析失败: {e}") from e

    def synthesize(self, topic: str, notes: list[ExtractedNote]) -> Report:
        numbered = []
        for i, n in enumerate(notes, 1):
            numbered.append(f"[{i}] {n.text}（来源：{n.source_title} {n.source_url}）")
        data = self.llm.chat_json(
            SYNTHESIZER_SYSTEM,
            f"研究主题：{topic}\n\n全部要点：\n" + "\n".join(numbered) + "\n\n引用编号与上面的 [i] 对应。")
        try:
            return Report.model_validate(data)
        except ValidationError as e:
            raise AgentError(f"报告输出解析失败: {e}") from e
