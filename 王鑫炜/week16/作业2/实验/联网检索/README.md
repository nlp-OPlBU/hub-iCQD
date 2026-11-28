# 联网检索实测

2026-11-26 10:46:30（北京时间），Python 脚本从 Google News RSS 发出 4 次查询并保存原始响应。执行退出码为 0，返回状态为 `ok`，共保留 12 条去重候选。

| 查询 | RSS 返回条数 | 每条查询最多保留 5 条 | 观察 |
| --- | ---: | ---: | --- |
| 中国神华，2026 年 10 月 | 22 | 5 | 命中公司财报、分红等线索，需继续定位公司公告并筛选 ESG 相关性 |
| 煤炭甲烷排放，2026 年 10 月 | 2 | 2 | 命中行业气候披露与国外油气报道，行业对象与政策适用范围待核 |
| 碳排放中标，2024-11-26 至 2026-11-26 | 10 | 5 | 能发现能耗与碳排放替代方案咨询线索，同时夹有海外市场与工程建设报道 |
| ESG、碳排放核算、中标，2026-11-18 至 11-26 | 0 | 0 | 提供方对此检索式返回空 RSS，政府采购公告的覆盖需要另行核查 |

[查询文件](查询.json)保存原始查询词和日期运算符。[候选摘要](候选摘要.md)由摘要脚本生成。[联网结果](联网结果.json)记录返回条数、原始排名、摘要、日期、链接和查询来源。[原始响应](原始响应/)按查询顺序保存，可复核解析过程。Google 新闻聚合地址统一标为 `is_aggregator=true`，复核时应按标题找到原文并核对日期、项目状态和金额。

## 运行与复跑

在 `作业2` 目录运行。下列联网命令将输出到 `/tmp`，便于保留这次实验的存档。

```bash
python3 scripts/search_web.py --provider google-news \
  --query-file '实验/联网检索/查询.json' --limit 5 \
  --capture-dir /tmp/esg-search-responses --out /tmp/esg-search-online.json
```

本次测试的托管沙箱默认禁止 Python 解析外部域名，联网命令在批准后运行成功。可在无需联网的环境中复跑已保存响应。

```bash
python3 scripts/search_web.py --provider google-news \
  --query-file '实验/联网检索/查询.json' --limit 5 \
  --response-dir '实验/联网检索/原始响应' \
  --captured-at '2026-11-26T10:46:30+08:00' --out /tmp/esg-search-replay.json
```

已对照联网和回放的 JSON，12 条候选、先后顺序、查询归属和查询统计均一致。联网文件的 `mode` 为 `online`，回放文件为 `replay`。

## 提供方与诊断

- `google-news` 默认无需密钥，覆盖其新闻索引。
- `brave` 要求运行环境提供 `BRAVE_SEARCH_API_KEY`。可用 `--freshness 2026-10-01to2026-10-31` 按其索引日期预筛，并以官网原文日期做最终判断。作业用模拟响应检验了 Brave 解析与去重。 [Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get)
- `bing-rss` 无需密钥，本次中文探测曾返回与主题无关的结果。使用时应逐条核实相关性。
- `status=ok` 表示所有查询有可解析响应，可能含 0 条候选。`partial` 表示部分查询失败。`failed` 表示全部失败并返回退出码 1。错误详情保存在 `errors`。HTTP 429 时遵守响应中的等待时间再试。
