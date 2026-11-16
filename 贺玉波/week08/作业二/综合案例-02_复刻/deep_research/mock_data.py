"""离线 mock 模式的内置假响应，字段与 models.py 的 Pydantic 模型对齐。"""

DEFAULT_PLAN = {
    "sub_questions": [
        {"id": "sq1", "question": "主题的基本现状是什么？"},
        {"id": "sq2", "question": "该主题的关键争议与趋势是什么？"},
    ],
    "search_queries": {
        "sq1": ["主题 现状", "主题 基本情况"],
        "sq2": ["主题 争议", "主题 趋势"],
    },
    "suggested_rounds": 1,
}

DEFAULT_READ = [
    {"text": "这是第一条带来源的要点。", "source_url": "https://example.com/a",
     "source_title": "示例来源A", "sub_question_id": "sq1"},
    {"text": "这是第二条带来源的要点。", "source_url": "https://example.com/b",
     "source_title": "示例来源B", "sub_question_id": "sq1"},
]

DEFAULT_REFINE_NEED_MORE = {
    "need_more": True,
    "reason": "缺少最新数据",
    "extra_queries": ["主题 最新数据"],
}

DEFAULT_REFINE_DONE = {"need_more": False, "reason": "信息已充足", "extra_queries": []}

DEFAULT_REPORT = {
    "title": "「测试主题」研究报告",
    "summary": "这是一份 mock 模式生成的示例报告摘要。",
    "sections": [
        {"heading": "背景与现状", "body": "正文内容，包含引用[1]。", "citation_ids": ["1"]},
        {"heading": "关键争议与趋势", "body": "正文内容，包含引用[2]。", "citation_ids": ["2"]},
    ],
    "conclusions": [
        {"text": "结论一：有来源支持。", "citation_ids": ["1"], "confidence": "高"},
        {"text": "结论二：有来源支持。", "citation_ids": ["2"], "confidence": "中"},
    ],
    "open_questions": ["遗留问题一", "遗留问题二"],
    "confidence": {
        # overall / info_cutoff_time / notes 由 pipeline 按来源数与来源日期确定性计算
        "unsourced_claims": [{"text": "这条结论无来源，属于模型推断。", "label": "模型推断"}],
    },
}
