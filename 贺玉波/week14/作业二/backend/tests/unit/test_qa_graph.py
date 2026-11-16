"""问答编排的纯逻辑单测：不依赖 LangGraph 运行时以外的任何中间件。"""

from __future__ import annotations

from app.graph.qa_graph import build_context, build_messages, needs_retrieval


def test_greeting_skips_retrieval() -> None:
    assert needs_retrieval("你好") is False
    assert needs_retrieval("hi") is False
    assert needs_retrieval("谢谢") is False


def test_real_question_triggers_retrieval() -> None:
    assert needs_retrieval("这份文档讲了什么") is True
    assert needs_retrieval("多模态检索的原理") is True


def test_build_context_numbers_hits() -> None:
    hits = [
        {"chunk_id": "c1", "doc_id": "d" * 32, "page_no": 2, "text": "第一段", "score": 0.03},
        {"chunk_id": "c2", "doc_id": "d" * 32, "page_no": 5, "text": "第二段", "score": 0.02},
    ]
    context, citations = build_context(hits)
    # 没有文件名时退化为 doc_id 前 8 位
    assert context.startswith("[1] 《dddddddd》第 2 页")
    assert "[2]" in context
    assert [item["index"] for item in citations] == [1, 2]
    assert citations[0]["page_no"] == 2


def test_build_context_collapses_same_page() -> None:
    # 同一页的多个 chunk 与图片折成一条，避免模型反复复述同一页内容
    hits = [
        {"chunk_id": "a", "doc_id": "d" * 32, "page_no": 7, "text": "前半段", "sources": ["text"], "score": 0.03},
        {"chunk_id": "b", "doc_id": "d" * 32, "page_no": 7, "text": "后半段", "sources": ["text"], "score": 0.02},
        {
            "chunk_id": "i",
            "doc_id": "d" * 32,
            "page_no": 7,
            "image_path": "/d/p007.png",
            "text": "",
            "sources": ["image"],
            "score": 0.01,
        },
    ]
    context, citations = build_context(hits)
    assert len(citations) == 1
    assert "前半段" in context and "后半段" in context
    assert citations[0]["image_index"] == 1
    assert citations[0]["sources"] == ["text", "image"]


def test_build_context_uses_doc_title() -> None:
    hits = [
        {
            "chunk_id": "c1",
            "doc_id": "d" * 32,
            "doc_title": "课程讲义.pdf",
            "page_no": 3,
            "text": "正文",
        }
    ]
    context, citations = build_context(hits)
    assert context.startswith("[1] 《课程讲义.pdf》第 3 页")
    assert citations[0]["doc_title"] == "课程讲义.pdf"


def test_build_context_respects_budget() -> None:
    hits = [{"chunk_id": f"c{i}", "doc_id": "d", "page_no": 1, "text": "字" * 800} for i in range(10)]
    context, citations = build_context(hits, budget=1000)
    assert len(context) <= 1100
    assert len(citations) < len(hits)


def test_build_context_keeps_image_only_hit() -> None:
    hits = [{"chunk_id": "img-1", "doc_id": "d", "image_path": "/d/a.png", "text": ""}]
    context, citations = build_context(hits)
    # 图片不再只给路径，而是打【图N】编号，生成侧按编号把图插在资料后
    assert "【图1】" in context
    assert citations[0]["image_index"] == 1
    assert citations[0]["image_path"] == "/d/a.png"


def test_build_messages_trims_history_by_token_budget() -> None:
    # 每条 100 字符 ≈ 25 token，一问一答约 50 token；预算 60 只装得下最新一轮
    history = []
    for index in range(4):
        history.append({"role": "user", "content": f"{index}" + "a" * 99})
        history.append({"role": "assistant", "content": f"{index}" + "b" * 99})

    messages = build_messages("问题", "【资料1】正文", history, history_budget=60)

    assert messages[0]["role"] == "system"
    assert [item["role"] for item in messages[1:-1]] == ["user", "assistant"]
    assert messages[1]["content"].startswith("3")
    assert "【资料】" in messages[-1]["content"]


def test_build_messages_injects_summary() -> None:
    messages = build_messages("问题", "资料", [], summary="用户在问多模态检索的实现")
    assert messages[1]["role"] == "system"
    assert "【对话摘要】" in messages[1]["content"]


def test_build_messages_without_context() -> None:
    messages = build_messages("你好", "", [], has_context=False)
    assert messages[-1]["content"] == "你好"


def test_build_messages_interleaves_images_with_labels() -> None:
    images = [
        {"url": "data:image/png;base64,AAA", "label": "【图1｜《讲义.pdf》第 3 页】"},
        {"url": "data:image/png;base64,BBB", "label": "【图2｜《讲义.pdf》第 8 页】"},
    ]
    messages = build_messages("图里画了什么", "【资料1】正文", [], images=images)
    parts = messages[-1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "text" and "【资料】" in parts[0]["text"]
    # 每张图紧跟在属于自己的标签后面，最后才是问题
    assert parts[1]["text"] == "【图1｜《讲义.pdf》第 3 页】"
    assert parts[2]["image_url"]["url"] == "data:image/png;base64,AAA"
    assert parts[3]["text"] == "【图2｜《讲义.pdf》第 8 页】"
    assert parts[4]["image_url"]["url"] == "data:image/png;base64,BBB"
    assert parts[-1]["type"] == "text" and "【问题】" in parts[-1]["text"]


def test_build_messages_with_user_image_only() -> None:
    messages = build_messages("", "", [], has_context=False, user_image="data:image/png;base64,CCC")
    parts = messages[-1]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url", "text"]
    assert "用户本轮上传的图片" in parts[0]["text"]
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,CCC"
    assert "请描述并分析这张图片" in parts[-1]["text"]
