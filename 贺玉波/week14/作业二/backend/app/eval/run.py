"""评测入口：``python -m app.eval.run --file ../docs/eval/golden.jsonl``。

只评测**检索**这一段（不调生成模型）：
- 生成质量需要 LLM 打分，成本高且主观，放在人工抽检；
- 检索质量是 RAG 的地基，召回不对，再好的模型也只能编。

指标口径见 ``app.eval.metrics``；报告默认写 ``docs/eval/report.json``，便于版本间对比。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.eval.metrics import aggregate, hit_at_k, mrr, ndcg_at_k, percentile, recall_at_k
from app.services import retrieval_service


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            item = json.loads(line)
            if not item.get("query") or not item.get("relevant"):
                raise ValueError(f"{path}:{line_no} 缺少 query 或 relevant")
            rows.append(item)
    return rows


def evaluate(rows: list[dict[str, Any]], *, top_k: int, with_images: bool) -> dict[str, Any]:
    details = []
    latencies: list[float] = []
    for item in rows:
        started = time.perf_counter()
        hits, _ = retrieval_service.search(
            item["query"],
            top_k,
            with_images=with_images,
            doc_id=item.get("doc_id"),
        )
        latencies.append((time.perf_counter() - started) * 1000)
        retrieved = [hit["chunk_id"] for hit in hits]
        relevant = item["relevant"]
        details.append(
            {
                "query": item["query"],
                "retrieved": retrieved,
                "miss": sorted(set(relevant) - set(retrieved[:top_k])),
                "hit": hit_at_k(retrieved, relevant, top_k),
                "recall": recall_at_k(retrieved, relevant, top_k),
                "mrr": mrr(retrieved, relevant),
                "ndcg": ndcg_at_k(retrieved, relevant, top_k),
            }
        )
    return {
        "top_k": top_k,
        "queries": len(rows),
        "metrics": aggregate(details),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.5), 1),
            "p95": round(percentile(latencies, 0.95), 1),
        },
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--file", default="../docs/eval/golden.jsonl")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--with-images", action="store_true", help="启用图片通道（需要 CLIP 与图像集合）")
    parser.add_argument("--report", default="../docs/eval/report.json")
    parser.add_argument("--min-recall", type=float, default=0.0, help="低于该值以非 0 退出，供 CI 使用")
    args = parser.parse_args(argv)

    golden = Path(args.file)
    if not golden.exists():
        print(f"找不到标注文件 {golden}，请参考 docs/eval/golden.example.jsonl 编写", file=sys.stderr)
        return 2

    rows = load_rows(golden)
    if not rows:
        print(f"{golden} 里没有有效样本", file=sys.stderr)
        return 2

    retrieval_service.ensure_ready()
    result = evaluate(rows, top_k=args.top_k, with_images=args.with_images)
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["golden"] = str(golden)

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics = result["metrics"]
    print(f"样本 {result['queries']} 条  top_k={args.top_k}  图片通道={'开' if args.with_images else '关'}")
    print(
        "hit@{k}={hit:.3f}  recall@{k}={recall:.3f}  mrr={mrr:.3f}  ndcg@{k}={ndcg:.3f}".format(
            k=args.top_k,
            hit=metrics["hit"],
            recall=metrics["recall"],
            mrr=metrics["mrr"],
            ndcg=metrics["ndcg"],
        )
    )
    print(f"检索延迟 p50={result['latency_ms']['p50']}ms  p95={result['latency_ms']['p95']}ms")
    print(f"报告已写入 {report}")

    if metrics["recall"] < args.min_recall:
        print(f"recall {metrics['recall']:.3f} 低于阈值 {args.min_recall}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
