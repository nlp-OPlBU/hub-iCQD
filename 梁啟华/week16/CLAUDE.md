# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目范围

本目录是作业仓库 `hub-iCQD` 中 `梁啟华/week16` 的提交内容：按 Anthropic Agent Skills 规范实现两个 ESG 领域技能。整个项目只依赖 Python 3.10+ 标准库，无构建、无依赖安装、无 lint 配置、无测试框架。

仓库根 `README.md` 的提交规则同样适用：只修改自己名下（`梁啟华/`）的文件，不得修改其他同学和老师的内容；新增文件名避免空格与 `.`，用 `-` 或 `_`。

## 常用命令

所有命令均以 `week16/` 或脚本所在技能目录为工作目录。**必须用 `python -I` 隔离模式运行**，避免当前目录的同名模块污染导入。

```bash
# 招投标流水线演示（在 skills/esg-tender-intel/ 下）
python -I scripts/normalize_dedup.py --input assets/sample-tenders-raw.csv --outdir ./.demo
python -I scripts/tag_tenders.py --input ./.demo/clean.csv --outdir ./.demo

# 幂等验证：对已清洗文件重跑，去重数应为 0
python -I scripts/normalize_dedup.py --input ./.demo/clean.csv --outdir ./.demo/rerun

# 月报结构校验（在 skills/esg-monthly-brief/ 下）
python -I scripts/check_brief.py assets/example-brief.md
```

预期基线（当前样例数据）：

- 归一化：输入 16 条 → 13 条，移除重复 3 条（18.75%），金额已知 12 条（覆盖 92.3%）；
- 打标：相关 9 条、噪声 4 条，服务类型分布见输出首行关键词范围（L1 19 / L2 41 / L3 20，阈值 3 分）；
- 月报校验：8 项全部 PASS，退出码 0。

不存在“运行单个测试”的概念：样例 `assets/` 与 `scripts/` 的自校验就是验证手段。改动脚本或规则后，重跑上述命令并对比输出。Windows 下输出目录可换成任意可写目录。

## 架构

### 渐进式披露（两个技能共用）

每个技能是一个目录，分四层，职责严格分离：

- `SKILL.md`：唯一入口。frontmatter（`name`、`description` 为官方字段，另有 `trigger_phrases`、`quality_gates` 等自定义字段）+ 可执行主流程 + 质量门表。只写“怎么做决策”，不堆细节。
- `references/`：细节文档，按 SKILL.md 的指引在对应步骤才加载（评分表、模板、分类体系、去重算法等）。
- `scripts/`：确定性计算，仅标准库，可重复运行。脚本结果是质量门的机器验证依据。
- `assets/`：机器可读规则 `keyword-rules.json` 与虚构样例数据。

修改时保持这个分层：不要把 reference 的大段细则复制回 `SKILL.md`，也不要把需要判断的规则硬编码进脚本而不同步文档。

### esg-tender-intel：数据处理流水线

```
原始 CSV/JSON
  └─ normalize_dedup.py → clean.csv + dedup_report.json
       └─ tag_tenders.py → tagged.csv + tag_summary.json
```

- `normalize_dedup.py`：项目编号 NFKC 归一化（去分隔符、大写）后去重；无编号时用“标题+采购人+发布月份”组合键。重复组保留字段完整度最高者。金额按 **中标 > 合同 > 预算** 取用并统一为万元，无单位按元推断并计数。
- `tag_tenders.py`：默认从脚本位置定位 `../assets/keyword-rules.json` 读取规则；相关性 = L1/L2/L3 加权命中 + 排除词扣分。仅命中 L3 或低于 `min_score` 判为噪声。服务类型取命中最多的一类为主标签，最多 3 个副标签，无命中为 `other`。
- 两个脚本都支持用 `--id-col/--title-col/...` 覆盖字段名，`tag_tenders.py` 支持 `--text-cols`、`--min-score`；派生列固定为 `amount_wan/amount_basis/dedup_key/source_rows` 与 `matched_*/relevance_score/service_type*/noise_*`。
- **幂等机制**：`normalize_dedup.py` 检测到输入已有 `amount_wan`/`amount_basis` 时直接复用，不再重新解析，因此对 `clean.csv` 重跑去重数必须为 0。改动金额或去重逻辑时不能破坏这一点。
- 规则改动需同步两处：`assets/keyword-rules.json`（脚本准绳，含 `version`）与 `references/keyword-library.md` / `service-taxonomy.md`（人工说明与披露模板）。

### esg-monthly-brief：生成与校验分离

`SKILL.md` 的执行流程固定为 S0 界定范围 → S1 扫描筛选 → S2 写传导判断 → S3 拆解行业实践 → S4 识别客户张力 → S5 同行对标 → S6 结论与跟踪点 → S7 自检交付。核心判断规则在 `references/workflow-detail.md`，输出骨架在 `references/output-template.md`。

`scripts/check_brief.py` 只校验结构，不校验事实，且对标题和要素有硬性要求：

- 四个二级标题必须可被匹配到（政策/行业实践/客户张力/月度结论）；
- 每条政策用 `###` 条目，含“传导”或“作用机制 + 对客户影响”，条数 3–5；
- 案例 ≥2 个，每个含问题/做法/成果/可复制条件四要素；
- 张力需有证据，或显式写“未识别到显著张力”并给出检索依据；
- 对标区需同时有 Markdown 表格和“对标”表述；
- 结论区 ≥3 个列表项为跟踪点，每项含时间词和“验证”；
- 全文链接 ≥5、`来源：` 标注 ≥3，且无占位符（`待补充`/`TBD`/`TODO`/`【待` 等）。

因此修改模板、示例或生成新简报时，先套用 `references/output-template.md` 的标题结构，再跑校验脚本。

## 内容与数据纪律

- **不编造**：政策、文号、企业数据、招标项目、金额、链接都不得凭记忆生成。无法联网或无数据源时，按 `SKILL.md` 要求显式声明缺口（月报）或要求用户提供导出文件（招投标）。
- `assets/example-brief.md` 与 `assets/sample-tenders-raw.csv` 是**虚构演示数据**，不得作为真实信息引用；样例中的“虚构”标注需保留。
- 输出与文件编码统一 UTF-8；CSV 使用 `utf-8-sig` 以兼容 Excel；脚本已自行把 stdout/stderr 重设为 UTF-8（Windows 控制台默认 GBK 会导致中文乱码）。
- 两个 `SKILL.md` 的 frontmatter 自定义字段是作业设计的一部分；只有在需要满足只允许 `name`/`description` 的严格校验环境时，才整体移入 `metadata:`。
