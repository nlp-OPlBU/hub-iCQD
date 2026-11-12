"""命令行入口：问题从参数或 stdin 读取，最终 JSON 输出 stdout，日志输出 stderr。

支持 Human-in-the-loop：clarify 节点中断后，可在终端回答，或用 --answer 直接恢复。

用法（在"作业一"目录下）：
    python run.py "总共有多少张专辑？"
    python run.py --mock "总共有多少张专辑？"
    python run.py --mock --scenario repair "总共有多少张专辑？"
    python run.py --mock --scenario clarify "那个歌手的总销量最高？"
    python run.py --mock --scenario clarify --answer "按销量" "那个歌手的总销量最高？"
    python run.py --sql "SELECT COUNT(*) AS album_count FROM Album;" --mock
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from .config import settings
from .graph import AgentRun, SQLAgentGraph


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangGraph 版 SQL Agent（Chinook / SQLite）")
    parser.add_argument("question", nargs="*", help="自然语言问题；省略时从 stdin 读取")
    parser.add_argument(
        "--log-level",
        default=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="离线模式：不调用外部模型，用确定性规则驱动同一张 LangGraph 图",
    )
    parser.add_argument(
        "--scenario",
        default=os.getenv("SQL_AGENT_MOCK_SCENARIO", "valid"),
        choices=["valid", "repair", "clarify"],
        help="mock 场景：valid 首次成功；repair 故意失败演示修复；clarify 演示 HITL 中断与恢复",
    )
    parser.add_argument(
        "--answer",
        help="clarify 中断时的人工回答；提供后自动恢复执行",
    )
    parser.add_argument(
        "--sql",
        help="跳过选表/生成，直接把给定 SQL 送入校验与执行节点",
    )
    parser.add_argument(
        "--tables",
        help="配合 --sql 指定涉及的表，逗号分隔；省略时按表名从 SQL 中推断",
    )
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=None,
        help=f"最大修复次数，默认 {settings.max_repair_attempts}",
    )
    parser.add_argument(
        "--show-trace",
        action="store_true",
        help="额外输出 LangGraph 节点执行轨迹（JSON: {result, trace}）",
    )
    return parser


def _prompt_clarification(interrupt: dict) -> str:
    """交互式终端里把澄清问题与选项打印到 stderr，再读取回答。"""
    question = str(interrupt.get("question") or "")
    options = interrupt.get("options") or []
    print("\n需要人工澄清：", file=sys.stderr)
    print(f"  {question}", file=sys.stderr)
    for index, option in enumerate(options, start=1):
        print(f"  [{index}] {option}", file=sys.stderr)
    prompt = "请选择序号或直接输入回答：" if options else "请输入回答："
    print(prompt, file=sys.stderr, end="", flush=True)

    try:
        answer = input().strip()
    except (EOFError, KeyboardInterrupt):
        # 伪终端 / 重定向输入时可能读不到内容，交给上层给出明确错误而不是挂死。
        return ""
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return str(options[int(answer) - 1])
    return answer


async def _execute(agent: SQLAgentGraph, question: str, args) -> AgentRun:
    tables = [item.strip() for item in (args.tables or "").split(",") if item.strip()]
    run = await agent.start_run(question, sql=args.sql, tables=tables or None)
    if not run.interrupted:
        return run

    answer = (args.answer or "").strip()
    if not answer and sys.stdin.isatty():
        answer = _prompt_clarification(run.interrupt or {})
    if not answer:
        raise PromptRequired(run)
    return await agent.resume_run(answer, thread_id=run.thread_id)


class PromptRequired(RuntimeError):
    """非交互环境没有提供 --answer 时，不能挂起等待输入。"""

    def __init__(self, run: AgentRun) -> None:
        super().__init__("流程停在 clarify 中断点，需要人工回答")
        self.run = run


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level)

    if args.question:
        question = " ".join(args.question)
    elif sys.stdin.isatty():
        print("请输入问题：", file=sys.stderr, end="", flush=True)
        question = input()
    else:
        question = sys.stdin.read().rstrip("\r\n")

    agent = SQLAgentGraph(
        max_repair_attempts=args.max_repair_attempts,
        mock=args.mock,
        mock_scenario=args.scenario,
    )
    try:
        run = asyncio.run(_execute(agent, question, args))
    except PromptRequired as stop:
        run = stop.run
        payload = {
            "thread_id": run.thread_id,
            "status": "interrupted",
            "interrupt": run.interrupt,
            "error": (
                "当前环境无法交互输入，请用 --answer 提供澄清回答后重试；"
                "终端中直接运行则会在中断处提示输入。"
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    payload = run.output.model_dump()
    if args.show_trace:
        payload = {"result": payload, "trace": run.trace}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
