"""python -m sql_agent '商店历史发票总销售额是多少？' 或 python -m sql_agent --demo rewrite"""

import argparse
import json
from pathlib import Path

from langchain_openai import ChatOpenAI

from common import Settings
from .agent import LangChainSQLAgent
from .database import Database
from .demo import make_demo


def main():
    parser = argparse.ArgumentParser(description="LangChain + Chinook 只读 SQL Agent")
    parser.add_argument("question", nargs="?", help="自然语言问题；在线模式必填")
    parser.add_argument("--demo", nargs="?", const="rewrite", choices=["count", "join", "rewrite"],
                        help="离线回放指定案例，实际执行 SQLite；不测试模型理解能力")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "data" / "chinook.db")
    parser.add_argument("--json", action="store_true", help="输出包括 SQL、查询结果和轨迹的完整 JSON")
    args = parser.parse_args()
    if args.demo and args.question:
        parser.error("--demo 使用固定案例，请不要同时传入自由问题。")
    if not args.demo and not args.question:
        parser.error("请提供问题，或使用 --demo 进行离线验证。")
    try:
        database = Database(args.db)
        if args.demo:
            question, model = make_demo(args.demo)
            mode = "offline_replay"
        else:
            settings = Settings.load()
            settings.validate_online()
            extra = {"enable_thinking": False} if settings.disable_thinking else {}
            model = ChatOpenAI(model=settings.sql_model, api_key=settings.api_key,
                               base_url=settings.base_url, temperature=0, timeout=settings.timeout,
                               max_retries=0, max_tokens=1800, extra_body=extra, use_responses_api=False)
            question, mode = args.question, "live"
        result = LangChainSQLAgent(database, model, mode=mode).run(question)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"启动失败：{exc}\n")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"模式：{result['mode']}\n状态：{result['status']}\n回答：{result['answer']}")
        for query in result["queries"]:
            print("\nSQL：", query["sql"])
            print("真实结果：", json.dumps(query["result"], ensure_ascii=False))
        print("\n工具错误次数：", result["tool_errors"])
    return 0 if result["status"] in {"ok", "no_query"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
