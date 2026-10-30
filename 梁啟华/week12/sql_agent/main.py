"""SQL Agent 命令行入口。

用法：
    python main.py --question "销售额最高的前 5 个国家是哪些？"
    python main.py                     # 进入交互式问答
"""

from __future__ import annotations

import argparse
import logging
import sys

from sql_agent import DEFAULT_DB_PATH, AgentResult, SQLAgent, SQLAgentError


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 chinook 数据库的 SQL Agent")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite 数据库路径，默认使用 04_SQL-Code-Agent-Demo/chinook.db",
    )
    parser.add_argument(
        "-q",
        "--question",
        default=None,
        help="只执行单个问题；不传则进入交互模式",
    )
    parser.add_argument(
        "--max-rewrites",
        type=int,
        default=2,
        help="SQL 校验失败后的最大改写次数，默认 2",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=200,
        help="单次查询最多返回的行数，默认 200",
    )
    parser.add_argument(
        "--no-semantic-check",
        action="store_true",
        help="跳过基于大模型的 SQL 语义校验",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只输出最终答案，不输出过程日志",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="打印完整工作流过程",
    )
    return parser


def print_result(result: AgentResult) -> None:
    print("\n" + "=" * 60)
    print(f"问题：{result.question}")
    print(f"选中表：{', '.join(result.tables)}")
    print(f"SQL（生成 {result.attempts} 次）：\n{result.sql}")
    print(f"返回行数：{result.row_count}" + ("（已截断）" if result.truncated else ""))
    print("-" * 60)
    print(f"回答：{result.answer}")
    print("=" * 60)


def print_trace(result: AgentResult) -> None:
    print("\n工作流过程：")
    for index, step in enumerate(result.trace, 1):
        print(f"{index}. [{step.stage}] {step.detail}")


def main() -> int:
    args = build_argument_parser().parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        agent = SQLAgent(
            db_path=args.db,
            max_rewrite_rounds=args.max_rewrites,
            max_rows=args.max_rows,
            semantic_check=not args.no_semantic_check,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 1

    try:
        if args.question:
            _run_once(agent, args.question, args.trace)
            return 0

        print("SQL Agent 已启动，输入问题开始查询，输入 exit/quit 退出。")
        while True:
            try:
                question = input("\n问题> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                break
            _run_once(agent, question, args.trace)
    finally:
        agent.close()

    return 0


def _run_once(agent: SQLAgent, question: str, trace: bool) -> None:
    try:
        result = agent.ask(question)
    except (SQLAgentError, ValueError, RuntimeError) as exc:
        print(f"\n查询失败：{exc}", file=sys.stderr)
        return
    print_result(result)
    if trace:
        print_trace(result)


if __name__ == "__main__":
    raise SystemExit(main())
