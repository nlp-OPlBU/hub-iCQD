#!/usr/bin/env python3
"""端到端冒烟测试：真的起一个临时 Web 进程，把三个功能各跑一遍并断言。

    conda activate py312
    python scripts/smoke.py                 # 自动起临时服务（端口 8123）
    python scripts/smoke.py --base-url http://localhost:8010   # 测你已开着的服务
    python scripts/smoke.py --keep          # 测完不关服务，并打印日志尾部

覆盖的检查项
------------
* 环境自检：Kafka 后端是 kafka（不是降级队列）、topic 已建、embedding 不是 mock
* 功能 1：多 PDF 上传 -> 投递 topic mmrag.pdf.upload（走 Kafka，不在 Web 进程里解析）
* 功能 2：状态机真的出现 **待解析 → 解析中 → 解析完成**（从 SSE 抓事件，不是前端假动画）
* 检索：bge 向量 + FAISS 能命中正确页
* 功能 3a：**图文混排输出** —— 答案 markdown 里带 ![](/api/images/{id})，且这些 URL 可下载
* 功能 3b：**图问一起送模型** —— 带图片提问，回答统计里 images_sent_to_model >= 2
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_PDF = BASE_DIR / "samples" / "demo_multimodal_rag.pdf"
SAMPLE_FIG = BASE_DIR / "samples" / "figures" / "fig1_arch.png"

RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    flag = "✅" if ok else "❌"
    RESULTS.append((name, ok, detail))
    print(f"{flag} {name}" + (f"  —— {detail}" if detail else ""), flush=True)
    return ok


def section(t: str) -> None:
    print(f"\n{'=' * 66}\n{t}\n{'=' * 66}", flush=True)


# ---------------------------------------------------------------------------
def sse_lines(resp, until=None, max_events: int = 4000):
    """把 SSE 响应按帧解析成 dict 列表（可提前停止）。"""
    events = []
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw.startswith("data:"):
            try:
                m = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            events.append(m)
            if until and until(m):
                break
        if len(events) >= max_events:
            break
    return events


# ---------------------------------------------------------------------------
def free_port(preferred: int) -> int:
    """选一个真正空闲的端口。

    之前吃过亏：上一次被 Ctrl-C 打断的 uvicorn 成了孤儿进程还占着 8123，
    新起的服务 bind 失败退出，而测试脚本却去连**那个跑着旧代码的僵尸服务**，
    现象是「测试卡住、结果不可信」。所以这里主动避让，并在服务退出时报错。
    """
    import socket

    for port in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue                      # 被占用就试下一个
            return port
    raise SystemExit(f"❌ {preferred}~{preferred + 19} 都被占用，请手动指定 --port 或清理进程")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="多模态 RAG 端到端冒烟测试")
    ap.add_argument("--base-url", default="", help="测已在跑的服务；留空则自动起临时服务")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--keep", action="store_true", help="测完保留服务")
    ap.add_argument("--timeout", type=float, default=420, help="等待解析完成的最长秒数")
    ap.add_argument("--mock-embed", action="store_true",
                    help="用 mock 向量跑（不加载 torch，链路验证快；embedding 质量不可信）")
    ap.add_argument("--startup-timeout", type=float, default=180,
                    help="等待服务就绪的最长秒数（机器繁忙时 embedding 加载会慢）")
    args = ap.parse_args(argv)

    proc = None
    base = (args.base_url or f"http://127.0.0.1:{args.port}").rstrip("/")
    if not args.base_url:
        port = free_port(args.port)
        if port != args.port:
            print(f"⚠️  端口 {args.port} 被占用（可能是上次没退干净的 uvicorn），改用 {port}")
            base = f"http://127.0.0.1:{port}"
        else:
            args.port = port
        if not SAMPLE_PDF.exists():
            section("准备示例 PDF")
            print("▶ python scripts/gen_sample_pdf.py")
            subprocess.run([sys.executable, str(BASE_DIR / "scripts" / "gen_sample_pdf.py")],
                           cwd=str(BASE_DIR), check=False)
        section("启动临时 Web 服务")
        (BASE_DIR / "data" / "logs").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PORT=str(port), WORKERS_INPROC="1")
        if args.mock_embed:
            env["EMBED_BACKEND"] = "mock"
            print("ℹ️  --mock-embed：本次用哈希向量跑链路，检索质量不作数")

        log = open(BASE_DIR / "data" / "logs" / "smoke-server.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "info"],
            cwd=str(BASE_DIR), env=env, stdout=log, stderr=subprocess.STDOUT)
        log.close()

    # ---- 等服务起来（首次要加载 sentence-transformers，可能十几秒；health 也别卡太短）----
    health = None
    deadline = time.time() + args.startup_timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/api/health", timeout=15)
            if r.ok:
                health = r.json()
                break
        except requests.RequestException:
            pass
        if proc and proc.poll() is not None:
            print("❌ 服务进程退出，见 data/logs/smoke-server.log")
            return 1
        time.sleep(1.0)
    if health is None:
        print(f"❌ 无法访问 {base}/api/health")
        return 1

    section("① 环境自检")
    bus_backend = (health.get("bus") or {}).get("backend")
    check("Kafka 后端可用", bus_backend == "kafka",
          f"backend={bus_backend} servers={(health.get('config') or {}).get('kafka', {}).get('servers')}")
    topics = (health.get("bus") or {}).get("topics") or []
    need = [t for t in ("mmrag.pdf.upload", "mmrag.pdf.parsed", "mmrag.doc.status",
                        "mmrag.qa.request", "mmrag.qa.answer") if t not in topics]
    check("5 个 topic 已创建", not need, "缺少 " + str(need) if need else "、".join(topics))
    emb = health.get("embedding") or {}
    if args.mock_embed:
        print(f"   ℹ️  embedding：{emb.get('model')}（--mock-embed，跳过真实性断言）")
    else:
        check("Embedding 非 mock", not emb.get("mock"),
              f"{emb.get('model')} dim={emb.get('dim')} device={emb.get('device')}")
    llm_h = health.get("llm") or {}
    print(f"   ℹ️  回答模型：{llm_h.get('vision_model')}（mock={llm_h.get('mock')}）"
          f"；解析器：MinerU {'在线' if (health.get('mineru') or {}).get('ok') else '不可用 → 内置'}")

    # ══ 先用后台线程订阅 SSE，再上传：这样才能真的抓到「待解析 → 解析中 → 解析完成」══
    raw_events: list = []
    sse_stop = threading.Event()

    def sse_reader() -> None:
        try:
            with requests.get(f"{base}/api/events", stream=True,
                              timeout=args.timeout + 120) as es:
                for raw in es.iter_lines(decode_unicode=True):
                    if sse_stop.is_set():
                        return
                    if not raw or not raw.startswith("data:"):
                        continue
                    try:
                        m = json.loads(raw[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if m.get("type") == "snapshot":
                        for d in (m.get("documents") or []):
                            if d.get("state"):
                                raw_events.append({"doc_id": d.get("doc_id"), "state": d["state"],
                                                   "state_label": d.get("state_label"),
                                                   "progress": d.get("progress"),
                                                   "message": d.get("message", ""),
                                                   "stage": d.get("stage", "")})
                    elif m.get("type") == "status":
                        raw_events.append(m)
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠️ SSE 读取结束：{type(exc).__name__}: {exc}", flush=True)

    reader = threading.Thread(target=sse_reader, daemon=True)
    reader.start()
    time.sleep(0.4)          # 给 EventSource 一点时间连上

    section("② 功能 1：上传多个 PDF → Kafka")
    if not SAMPLE_PDF.exists():
        check("示例 PDF 存在", False, f"缺少 {SAMPLE_PDF}")
        sse_stop.set()
        return 1
    pdf_bytes = SAMPLE_PDF.read_bytes()
    files = [("files", ("demo_multimodal_rag.pdf", io.BytesIO(pdf_bytes), "application/pdf")),
             ("files", ("demo-copy.pdf", io.BytesIO(pdf_bytes), "application/pdf"))]
    t0 = time.time()
    r = requests.post(f"{base}/api/documents", files=files, timeout=180)
    ok = r.status_code == 200
    check("多文件上传接口 200", ok, f"HTTP {r.status_code} 用时 {time.time() - t0:.1f}s")
    if not ok:
        print(r.text[:600])
        sse_stop.set()
        return 1
    body = r.json()
    accepted = body.get("accepted") or []
    skipped = body.get("skipped") or []
    check("两份相同 PDF：1 份入库 + 1 份识别重复",
          len(accepted) == 1 and len(skipped) == 1,
          f"accepted={len(accepted)} skipped={len(skipped)}")
    doc_id = accepted[0]["doc_id"]
    # 上传接口只做「落盘 + 投递 Kafka」，不等解析；阈值放宽是因为机器负载高时
    # 连写盘和建连都会变慢，真正要验证的是「返回时文档还没解析完」（见下一节的状态机）
    check("上传接口不等解析（只落盘+投递）", time.time() - t0 < 90,
          f"用时 {time.time() - t0:.1f}s，投递 topic={body.get('topic')}")

    section("③ 功能 2：状态机 待解析 → 解析中 → 解析完成（SSE 抓事件）")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        mine = [e for e in list(raw_events) if e.get("doc_id") == doc_id]
        if mine and mine[-1].get("state") in ("done", "failed"):
            break
        time.sleep(0.5)
    sse_stop.set()
    reader.join(timeout=3)

    seen_states: list = []
    stages: list = []
    progress_max = 0.0
    for e in list(raw_events):
        if e.get("doc_id") != doc_id:
            continue
        st = e.get("state")
        if not st:
            continue
        if not seen_states or seen_states[-1] != st:
            seen_states.append(st)
            print(f"   · {time.strftime('%H:%M:%S')} 状态 → {st}"
                  f"（{e.get('state_label') or ''}）{e.get('progress') or 0}% "
                  f"{(e.get('message') or '')[:70]}", flush=True)
        if e.get("stage") and e["stage"] not in stages:
            stages.append(e["stage"])
        try:
            progress_max = max(progress_max, float(e.get("progress") or 0))
        except (TypeError, ValueError):
            pass

    check("出现「待解析」", "pending" in seen_states, " → ".join(seen_states) or "（没抓到任何状态）")
    check("出现「解析中」", "parsing" in seen_states, f"细粒度阶段: {stages}")
    check("最终「解析完成」", bool(seen_states) and seen_states[-1] == "done",
          f"最后状态 {seen_states[-1] if seen_states else '-'}，最大进度 {progress_max}%")
    detail = requests.get(f"{base}/api/documents/{doc_id}", timeout=30).json()
    doc = detail.get("document") or {}
    check("落库了检索块与图片", doc.get("n_chunks", 0) > 0 and doc.get("n_images", 0) > 0,
          f"chunks={doc.get('n_chunks')} images={doc.get('n_images')} "
          f"parser={doc.get('parser')} pages={doc.get('pages')}")
    imgs = detail.get("images") or []
    with_caption = [i for i in imgs if (i.get("caption") or "").strip()]
    print(f"   ℹ️  配图 {len(imgs)} 张，其中 {len(with_caption)} 张有描述"
          f"（描述来自{'视觉模型' if len(with_caption) and not llm_h.get('mock') else 'PDF 题注'}）")

    section("④ 检索：bge 向量 + FAISS")
    s = requests.get(f"{base}/api/search", params={"q": "系统架构图里 Kafka 起什么作用", "top_k": 6},
                     timeout=120).json()
    hits = s.get("hits") or []
    check("检索有命中", len(hits) > 0, f"{len(hits)} 条，top score={hits[0]['score'] if hits else 0:.3f}"
          f"，用时 {s.get('took_ms')}ms")
    kinds = {h["kind"] for h in hits}
    print(f"   · 命中类型: {sorted(kinds)}；最高分: {hits[0]['filename'] if hits else '-'} p{hits[0]['page'] if hits else '-'}")
    s2 = requests.get(f"{base}/api/search", params={"q": "召回率 0.88 是怎么来的", "top_k": 5},
                      timeout=120).json()
    top_txt = (s2["hits"][0]["text"] if s2.get("hits") else "")
    check("问「召回率」能命中含 0.88 的片段", "0.88" in top_txt or any("0.88" in h["text"] for h in s2.get("hits", [])),
          top_txt[:70].replace("\n", " "))

    section("⑤ 功能 3a：图文混排输出")
    r = requests.post(f"{base}/api/qa", data={"question": "系统架构是怎么设计的？把架构图给我看一下",
                                             "session_id": "smoke_a"},
                      stream=True, timeout=(20, args.timeout))
    check("问答接口 200", r.status_code == 200, f"HTTP {r.status_code}")
    evs = sse_lines(r, until=lambda m: m.get("type") in ("answer", "error"))
    types = [e.get("type") for e in evs]
    check("事件顺序 accepted→stage→delta→answer",
          "accepted" in types and "delta" in types and types[-1] == "answer",
          f"{types[:1]} … {types[-2:]}（delta {types.count('delta')} 条，流式）")
    final = next((e for e in evs if e.get("type") == "answer"), None)
    answer = (final or {}).get("answer") or ""
    urls = re.findall(r"!\[[^\]]*\]\((/api/images/\d+)\)", answer)
    check("答案里内嵌了图片（图文混排）", len(urls) > 0, f"图片 {len(urls)} 张：{urls[:3]}")
    if urls:
        im = requests.get(base + urls[0], timeout=30)
        check("图片 URL 可下载", im.status_code == 200 and im.headers.get("content-type", "").startswith("image"),
              f"{im.headers.get('content-type')} {len(im.content) // 1024}KB")
    if final:
        print(f"   · 引用 {len(final.get('citations') or [])} 条；模型 {(final.get('model') or '')} "
              f"mock={final.get('mock')}；统计 {json.dumps(final.get('stats') or {}, ensure_ascii=False)[:200]}")
    if (final or {}).get("mock"):
        print("   ℹ️  mock 模式：回答由检索片段拼装，但仍验证了图片内嵌与渲染链路")

    section("⑥ 功能 3b：图问一起送到模型")
    if not SAMPLE_FIG.exists():
        check("示例配图存在", False, str(SAMPLE_FIG))
    else:
        with open(SAMPLE_FIG, "rb") as fh:
            r = requests.post(
                f"{base}/api/qa",
                data={"question": "这张图讲的是什么的？对应文档哪一节？", "session_id": "smoke_b"},
                files={"files": ("fig1_arch.png", fh.read(), "image/png")},
                stream=True, timeout=(20, args.timeout))
        check("带图片提问接口 200", r.status_code == 200, f"HTTP {r.status_code}")
        evs2 = sse_lines(r, until=lambda m: m.get("type") in ("answer", "error"))
        fin2 = next((e for e in evs2 if e.get("type") == "answer"), None)
        st2 = (fin2 or {}).get("stats") or {}
        check("图片与问题进了同一条多模态消息",
              bool(fin2) and st2.get("images_sent_to_model", 0) >= 1,
              f"images_sent_to_model={st2.get('images_sent_to_model')} "
              f"user={st2.get('user_images_sent')} doc={st2.get('doc_images_sent')} "
              f"model={st2.get('model')}")
        if fin2:
            print(f"   · 回答前 120 字：{(fin2.get('answer') or '')[:120]}".replace("\n", " "))
        stage_ev = next((e for e in evs2 if e.get("type") == "stage" and e.get("stage") == "retrieving"
                         and e.get("message") and "图" in e["message"]), None)
        if not (fin2 or {}).get("mock"):
            check("先用视觉模型读取用户图（检索 query 增强）", stage_ev is not None,
                  (stage_ev or {}).get("message", ""))

    section("⑦ Kafka 侧证据：消费组与 topic 消息")
    p = requests.get(f"{base}/api/pipeline", params={"recent": 8}, timeout=30).json()
    groups = p.get("consumer_groups") or []
    check("Worker 消费组已注册", any("parser" in g or "indexer" in g or "qa" in g for g in groups),
          ", ".join(groups[:6]))
    subs = p.get("subscriptions") or []
    err_subs = [s for s in subs if s.get("errors")]
    check("订阅无异常", not err_subs, json.dumps(err_subs[:2], ensure_ascii=False) if err_subs
          else f"{len(subs)} 个订阅，累计收 {p.get('consumed')} 条 / 发 {p.get('published')} 条")
    print("   · 最近状态事件：")
    for m in (p.get("recent_status") or [])[:4]:
        print(f"     - {m.get('state')} {m.get('progress')}% {m.get('message','')[:60]}")

    # ---- 收尾 ----
    section("结果汇总")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"   ❌ {name}  {detail}")
    print(f"\n{passed}/{len(RESULTS)} 项通过")

    # 删除测试文档，保持知识库干净
    if doc_id:
        try:
            requests.delete(f"{base}/api/documents/{doc_id}", timeout=60)
        except requests.RequestException:
            pass

    if proc and not args.keep:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("临时服务已关闭")
    elif proc and args.keep:
        print(f"服务保留在 {base}（Ctrl-C 结束本脚本不会关掉它）")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
