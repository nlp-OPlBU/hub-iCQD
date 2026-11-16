from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Report, RoundRecord
from .pipeline import ResearchOutput

_TEMPLATE_DIR = Path(__file__).parent / "assets"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(default=True, default_for_string=True),
)


def render_report_md(report: Report) -> str:
    lines = [f"# {report.title}", "", "## 摘要", "", report.summary, ""]
    for sec in report.sections:
        marks = "".join(f"[{c}]" for c in sec.citation_ids)
        lines += [f"## {sec.heading}{marks}", "", sec.body, ""]
    lines += ["## 关键结论", ""]
    for i, c in enumerate(report.conclusions, 1):
        marks = "".join(f"[{c}]" for c in c.citation_ids)
        lines.append(f"{i}. {c.text}{marks}（置信度：{c.confidence}）")
    lines += ["", "## 遗留问题", ""]
    lines += [f"- {q}" for q in report.open_questions]
    lines += ["", "## 置信度说明", ""]
    lines.append(f"- 总体置信度：{report.confidence.overall}")
    lines.append(f"- 信息截止时间：{report.confidence.info_cutoff_time or '未知'}")
    if report.confidence.notes:
        lines.append(f"- 说明：{report.confidence.notes}")
    for u in report.confidence.unsourced_claims:
        lines.append(f"- 以下内容为**{u.label}**：{u.text}")
    return "\n".join(lines)


def render_sources_md(report: Report) -> str:
    lines = [f"# 来源列表：{report.title}", ""]
    for c in report.citations:
        lines.append(f"{c.id}. [{c.title}]({c.url})")
    return "\n".join(lines)


def render_process_md(process: list[RoundRecord], topic: str) -> str:
    lines = [f"# 研究过程记录：{topic}", ""]
    for r in process:
        lines.append(f"## 第 {r.round_no} 轮（子问题 {r.sub_question_id}）")
        lines.append(f"- 检索关键词：{'、'.join(r.queries)}")
        lines.append(f"- 阅读页面：{r.pages_read} 条")
        lines.append(f"- 新发现要点：{r.new_findings} 条")
        if r.gap_decision.need_more:
            lines.append(f"- 补检判断：需要补充（{r.gap_decision.reason or '未说明'}）")
            if r.gap_decision.extra_queries:
                lines.append(f"- 补充检索词：{'、'.join(r.gap_decision.extra_queries)}")
        else:
            lines.append(f"- 补检判断：信息充足（{r.gap_decision.reason or ''}）")
        lines.append("")
    return "\n".join(lines)


def render_html(report: Report, process: list[RoundRecord]) -> str:
    template = _env.get_template("report_template.html")
    return template.render(report=report, process=process)


_RESERVED_WIN = {"con", "prn", "aux", "nul",
                 *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def slugify(topic: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s!！？]+', "_", topic).strip("_")
    s = s[:50].strip(". ")
    if not s or s.lower() in _RESERVED_WIN:
        return "research"
    return s


def write_outputs(output: ResearchOutput, topic: str,
                  out_dir: Path) -> dict[str, Path]:
    d = out_dir / slugify(topic)
    d.mkdir(parents=True, exist_ok=True)
    files = {
        "report": d / "report.md",
        "sources": d / "sources.md",
        "process": d / "process.md",
        "html": d / "report.html",
        "json": d / "output.json",
    }
    files["report"].write_text(render_report_md(output.report), encoding="utf-8")
    files["sources"].write_text(render_sources_md(output.report), encoding="utf-8")
    files["process"].write_text(
        render_process_md(output.process, output.report.title), encoding="utf-8")
    files["html"].write_text(render_html(output.report, output.process),
                             encoding="utf-8")
    files["json"].write_text(
        json.dumps(
            {
                "topic": topic,
                "report": output.report.model_dump(),
                "process": [r.model_dump() for r in output.process],
                "total_queries": output.total_queries,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return files
