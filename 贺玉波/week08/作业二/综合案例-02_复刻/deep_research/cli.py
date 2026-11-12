from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .llm import DeepSeekClient, MockLLM
from .pipeline import Pipeline
from .report import write_outputs
from .search import BochaSearchClient, MockSearchClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deep-research", description="深度研究助手：输入主题，输出带来源引用的研究报告")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help="每个子问题最多补充检索轮数（默认取配置 2）")
    parser.add_argument("--max-total-queries", type=int, default=None,
                        help="总检索次数上限（默认取配置 30）")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="输出目录（默认 outputs）")
    parser.add_argument("--mock", action="store_true",
                        help="离线 mock 模式：不调用真实 LLM/搜索 API")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.mock:
        llm = MockLLM()
        search = MockSearchClient()
    elif cfg.deepseek_api_key:
        if not cfg.bocha_api_key:
            print("未检测到 BOCHA_API_KEY。请在 .env 中配置，或使用 --mock 模式跑通流程。",
                  file=sys.stderr)
            return 1
        llm = DeepSeekClient(api_key=cfg.deepseek_api_key,
                             model=cfg.deepseek_model,
                             reasoning_effort=cfg.deepseek_reasoning_effort)
        search = BochaSearchClient(api_key=cfg.bocha_api_key,
                                   base_url=cfg.bocha_base_url)
    else:
        print("未检测到 DEEPSEEK_API_KEY。请在 .env 中配置，或使用 --mock 模式跑通流程。",
              file=sys.stderr)
        return 1

    pipeline = Pipeline(
        llm=llm, search=search,
        max_rounds=args.max_rounds if args.max_rounds is not None else cfg.max_rounds,
        max_total_queries=(args.max_total_queries
                           if args.max_total_queries is not None
                           else cfg.max_total_queries),
        on_event=lambda msg: print(msg, flush=True),
    )
    output = pipeline.run(args.topic)
    files = write_outputs(output, args.topic,
                          args.output_dir or cfg.output_dir)
    print(f"[完成] 共检索 {output.total_queries} 次")
    for kind, path in files.items():
        print(f"[输出] {kind}: {path}")
    return 0
