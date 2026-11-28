# 作业2 · 给 Skill 接入确定性脚本

| 环节 | 实现 | 输出 |
| --- | --- | --- |
| 发现候选 | [search_web.py](scripts/search_web.py) 请求搜索提供方，清理摘要、合并重复链接、记录查询与错误 | 搜索快照 JSON |
| 抽取线索 | [summarize_esg.py](scripts/summarize_esg.py) 对候选摘要按句切分、截取并保留链接 | 待核验线索 Markdown |
| 原文核验 | 使用作业1对应 Skill 确认主体、日期、公告状态、金额含义和边界，填入证据表 | 已核验证据 JSON |
| 生成底稿 | 同一个摘要脚本按日期筛选、归档、去重、摘取事实、计算金额和筛选日报 | 月报底稿、中标数字表、每日推送稿 |
| 形成报告 | Skill 使用底稿与原文完成影响、建议、可比性和数据限制判断 | 面向业务读者的报告 |

作业1的两个 Skill 可以独立运行。脚本模式继续使用同一个 Skill，将检索快照和核验后的证据交给程序，再根据原文完成报告。脚本只依赖 Python 3.9+ 标准库。请在 **作业2** 目录运行下列命令。

## 联网检索与初步摘要

```bash
python3 scripts/search_web.py \
  --provider google-news \
  --query '中国神华 after:2026-10-01 before:2026-11-01' \
  --query 'ESG 碳排放 核算 中标 after:2026-11-18' \
  --limit 5 --out /tmp/week16-search.json

python3 scripts/summarize_esg.py candidates /tmp/week16-search.json \
  --out /tmp/week16-candidates.md
```

Google News RSS 免密钥，返回的新闻聚合链接用于发现。需要更通用的网页检索时可切换至 `--provider brave`，在环境中提供 `BRAVE_SEARCH_API_KEY`，并按需传入 `--freshness 2026-10-01to2026-10-31`；支持 `--provider bing-rss` 作为另一种查询方式。对于中文政府采购，按作业1的来源列表补查官方平台和机构官网。搜索返回空结果、部分失败或全部失败时，分别记录，必要时调整检索策略。[Brave Web Search 参数](https://api-dashboard.search.brave.com/api-reference/web/search/get)

新运行可用 `--capture-dir` 保存提供方响应。已有响应可传给 `--response-dir` 重放，重复传入同一组查询或使用 `--query-file` 读取查询数组。联网试跑的实际输入、状态和结果见 [实验记录](实验/对照报告.md)。

### 从候选到已核验证据

参考 [月报证据](数据/月报证据.json) 或 [招投标证据](数据/招投标证据.json) 的结构。每个 JSON 顶层含 `case`、`subject`、`start`、`end`、`as_of` 和 `records`。日期使用 `YYYY-MM-DD`。一条证据包含唯一 `id`、原文 `url`、`title`、`publisher`、业务日期 `date`、`category` 和可溯源的事实 `text`。

- 月报使用 `政策与标准`、`行业与最佳实践`、`公司动态`、`同业行动` 四类。采用生效、反馈截止等窗口内里程碑时，填 `date_basis` 交代日期口径。
- 招投标用 `notice_type` 区分 `award`、`tender`、`candidate`、`cancelled`、`amendment`。尽量用一贯的项目标识 `project_id` 和包号 `lot`。编号跨平台可能冲突时，应统一公告与转载的标识，并加采购人或平台前缀。跨来源无法确认关联时保留线索供人工核验。
- `amount_kind` 为 `award_total` 时才计算 `amount_cny`。该字段采用人民币元十进制字符串，如 `"250000.00"`。预算、单价、费率、最高限价和未披露保留其各自类型。
- 日报填 `buyer`、`deadline`、`status`，需要报名或获取文件时另填 `registration_deadline`，有该前置条件而时间不明时填 `needs_registration: true`。带时间的字段均附时区。同一天的截点复盘建议写入原文的 `published_at`。只有公开且经核验仍可参与的公告才标 `open`。同包出现终止、中标或更正，应将原公告与更新一并提供。只有发布日期而缺少同日发布时刻时，处理更新采用保守顺序。

## 复跑正式底稿和测试

```bash
python3 scripts/summarize_esg.py monthly 数据/月报证据.json
python3 scripts/summarize_esg.py awards 数据/招投标证据.json
python3 scripts/summarize_esg.py daily 数据/招投标证据.json \
  --since 2026-11-25 --at 2026-11-26T10:00:00+08:00
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

可用 `--out 路径.md` 写入 UTF-8 文件。金额档位默认与 Skill 一致，可用 `--bins-wan 10 50 100 500` 指定 A/B 实验分箱。连续推送时由作业1的招标 Skill 按成功交付水位和状态去重，脚本生成当次推送正文。失败查询保留在快照及候选摘要，成功得到 0 条仅表示该查询没有返回匹配。

[查看两组实际输出与对照结论](实验/对照报告.md)
