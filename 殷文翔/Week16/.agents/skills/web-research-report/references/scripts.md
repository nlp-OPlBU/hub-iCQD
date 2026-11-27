# 批量检索与报告脚本

以下命令从 Skill 目录执行。脚本使用 Python 3.10 或更新版本的标准库。命令中的输入输出可用绝对路径。输出默认可放在工作区，若当前临时目录支持同目录原子替换，也可以用作输出目录。

## 并行检索

[search_web.py](../scripts/search_web.py) 使用已配置的 BRAVE_SEARCH_API_KEY 调用 Brave Web Search。批量提交前将查询分到实际研究维度，相同查询只请求一次。默认两个并发，每个查询返回最多八条候选。查询服务可能消耗 API 配额。只向服务发送公开检索词。

查询文件 queries.json 是一个数组。例如

    [
      {"group": "政策进展", "query": "网站无障碍 主管部门 最新规范"},
      {"group": "行业实践", "query": "网站无障碍 改造 实测 案例"}
    ]

    python -B scripts/search_web.py --queries queries.json --output search.json --workers 2
    python -B scripts/search_web.py --queries queries.json --output search.json --freshness 2026-10-27to2026-11-26

可用 --search-lang 和 --country 缩小地区或语言。--freshness 是搜索引擎索引的时间过滤，原文时效仍需核验。每个查询单独记录结果和错误。若全部失败，命令写出诊断 JSON 并返回非零状态。若没有密钥、网络权限或需要不同数据来源，使用环境的内置联网工具，按下述格式录入候选结果。

## 候选筛选

[triage_sources.py](../scripts/triage_sources.py) 读取 search.json。如果数据来自内置联网工具，按查询结果原有顺序填写 runs 数组。

    {
      "runs": [
        {
          "group": "行业实践",
          "query": "网站无障碍 改造 实测 案例",
          "results": [
            {
              "url": "https://example.org/practice?id=42",
              "title": "案例标题",
              "snippet": "用于筛选的检索摘要",
              "date_hint": "搜索页面显示的时间"
            }
          ]
        }
      ]
    }

    python -B scripts/triage_sources.py search.json --output shortlist.json --limit 8
    python -B scripts/triage_sources.py search.json --output shortlist.json --start 2026-10-27 --end 2026-11-26 --prefer-domain example.org

published 是可选字段。仅在原始页面已明确核对为发布日期时，填写 YYYY-MM-DD。日期窗两端包含在内，确认越界的记录放入 outside_window。未知日期和日期冲突保留待查，搜索结果自带的 date_hint 不用于硬过滤。脚本按查询排名与覆盖次数排序，--prefer-domain 仅做轻量加权。候选记录保留清理后的 URL 与检索时的原始地址。先打开各组候选验证，再按证据和相关性撰写正文。若某组为空或返回错误，定向补搜。

## 报告生成

[render_report.py](../scripts/render_report.py) 读取研究简报 brief.json。把阅读原文后核验的来源和写好的条目放入简报。可按主题设置章节，scope 可选。事实和分析必须引用简报中存在的来源 ID，建议和资料限制可按需引用。正式报告中给已确认的来源填写发布日期。下面的地址和内容用于展示结构。

    {
      "title": "网站无障碍实践调研",
      "scope": "范围为公开披露的改造与评估案例",
      "sources": {
        "s1": {"title": "实践案例原文", "url": "https://example.org/practice?id=42", "published": "2026-11-10"}
      },
      "sections": [
        {
          "title": "关键发现",
          "items": [
            {"kind": "fact", "text": "案例披露了键盘导航测试的覆盖范围。", "sources": ["s1"]},
            {"kind": "analysis", "text": "已披露的范围可以用于比较测试覆盖，但还需要核实通过率。", "sources": ["s1"]},
            {"kind": "recommendation", "text": "后续比较时记录每个站点的测试范围和通过率。"}
          ]
        }
      ]
    }

    python -B scripts/render_report.py brief.json --output report.md

item.kind 支持 fact、analysis、recommendation 和 limitation。输出按第一次引用顺序为来源编号，并在正文和来源索引中生成可点击链接。脚本校验引用完整性。综述、冲突处理和原文核验由运行 Skill 的代理完成。

[Brave Web Search API 文档](https://api-dashboard.search.brave.com/api-reference/web/search/get)

