from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .chains import ModelContractError
from .config import Settings
from .graph import SqlAgentGraph, WorkflowError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用自然语言只读查询 Chinook 数据库（LangChain 版）"
    )
    parser.add_argument("question", nargs="*", help="要查询的中文问题")
    parser.add_argument(
        "--verbose", action="store_true", help="输出七阶段结构化运行日志"
    )
    return parser


async def run(question: str, settings: Settings) -> str:
    """保持业务层异步，仅在 CLI 最外层创建事件循环。"""
    graph = SqlAgentGraph(settings)
    result = await graph.answer(question)
    return result.answer


def main() -> None:
    """解析命令行并将内部异常转换为简洁、稳定的退出码。"""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
    )
    if args.verbose:
        logging.getLogger("sql_agent_lc").setLevel(logging.INFO)

    question = " ".join(args.question).strip()
    if not question:
        try:
            question = input("请输入问题：").strip()
        except EOFError:
            question = ""
    if not question:
        print("问题不能为空。", file=sys.stderr)
        raise SystemExit(2)

    try:
        settings = Settings.from_env()
        answer = asyncio.run(run(question, settings))
    except (ValueError, ModelContractError) as error:
        print(f"配置或模型错误：{error}", file=sys.stderr)
        raise SystemExit(2) from error
    except WorkflowError as error:
        print(error.public_message, file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        raise SystemExit(130)

    print(answer)


if __name__ == "__main__":
    main()
