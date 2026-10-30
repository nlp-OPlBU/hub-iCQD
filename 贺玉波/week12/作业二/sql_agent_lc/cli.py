"""命令行入口：问题从参数或 stdin 读取，最终 JSON 输出 stdout，日志输出 stderr。

用法（在"作业二"目录下）：
    python -m sql_agent_lc "总共有多少张专辑？"
    python -m sql_agent_lc --log-level DEBUG "销售额最高的前 5 位客户是谁？"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from .config import settings
from .workflow import SQLAgentWorkflow


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LangChain 版 SQL Agent（Chinook / SQLite）")
    parser.add_argument("question", nargs="*", help="自然语言问题；省略时从 stdin 读取")
    parser.add_argument(
        "--log-level",
        default=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO",
    )
    args = parser.parse_args()

    _configure_logging(args.log_level)

    if args.question:
        question = " ".join(args.question)
    elif sys.stdin.isatty():
        print("请输入问题：", file=sys.stderr, end="", flush=True)
        question = input()
    else:
        question = sys.stdin.read().rstrip("\r\n")

    result = asyncio.run(SQLAgentWorkflow().run(question))
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
