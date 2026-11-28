# Week 16 · 两个 ESG 领域 Agent Skills

本作业在 `week16/skills/` 下实现两个按 Anthropic Agent Skills 规范组织的技能，均采用
**渐进式披露**：`SKILL.md` 只保留可执行的主流程，细节放入 `references/`，确定性计算交给 `scripts/`。

| 技能 | 定位 | 核心方法 |
| --- | --- | --- |
| `esg-monthly-brief` | 月度 ESG 监测简报 | “传导判断 + 张力识别”，而非信息罗列 |
| `esg-tender-intel` | ESG/双碳招投标情报 | 三层关键词库 + 服务分类 + 项目编号去重 + 金额口径统一 |

## 目录结构

```text
week16/
├── README.md
└── skills/
    ├── esg-monthly-brief/
    │   ├── SKILL.md
    │   ├── references/
    │   │   ├── workflow-detail.md      # 影响评分、传导链、张力、对标细则
    │   │   ├── output-template.md      # 四段式月报模板
    │   │   ├── source-guide.md         # 信源分级与检索关键词
    │   │   └── quality-checklist.md    # 6 条质量门判定标准
    │   ├── scripts/
    │   │   └── check_brief.py          # 月报结构与要素机器校验
    │   └── assets/
    │       └── example-brief.md        # 虚构示例（仅演示）
    └── esg-tender-intel/
        ├── SKILL.md
        ├── references/
        │   ├── keyword-library.md      # 三层关键词库说明
        │   ├── service-taxonomy.md     # 八类服务分类体系
        │   ├── dedup-and-amount.md     # 去重与金额口径
        │   └── coverage-and-noise.md   # 覆盖率声明、噪声规则、输出模板
        ├── scripts/
        │   ├── normalize_dedup.py      # 归一化 + 去重 + 金额统一
        │   └── tag_tenders.py          # 关键词打标 + 服务分类 + 噪声过滤
        └── assets/
            ├── keyword-rules.json      # 机器可读规则（脚本准绳）
            └── sample-tenders-raw.csv  # 虚构样例（含重复/噪声/混合单位）
```

## 触发方式

安装到 Claude Code 的 skills 目录后（或按你所用客户端的方式加载），可用自然语言触发：

- “生成本月 ESG 月报，以某煤炭集团为例” → `esg-monthly-brief`
- “汇总过去 24 个月 ESG 中标情况” → `esg-tender-intel`（模式 A）
- “每天推送双碳招标信息” → `esg-tender-intel`（模式 B）

两个技能的 `frontmatter` 均声明了 `trigger_phrases`、`inputs_required`、`output_structure`
（月报）或 `modes/shared_rules`（招投标），并各自定义了 `quality_gates`。

## 演示：招投标脚本流水线

样例数据为**虚构测试数据**，含 3 组重复、4 条噪声、混合金额单位（元/万元/亿元）。

```bash
cd skills/esg-tender-intel

# 1) 清洗：归一化项目编号、去重、金额统一为万元
python -I scripts/normalize_dedup.py \
  --input assets/sample-tenders-raw.csv --outdir /tmp/tender-demo

# 预期输出：
#   输入 16 条 -> 去重后 13 条，移除重复 3 条（重复率 18.75%）
#   重复组 3 个；金额已知 12 条（覆盖率 92.3%）

# 2) 打标：相关性打分、服务分类、噪声过滤
python -I scripts/tag_tenders.py \
  --input /tmp/tender-demo/clean.csv --outdir /tmp/tender-demo

# 预期输出：
#   相关 9 条，噪声 4 条；服务类型分布（碳核算 3、第三方审核 2、平台 1、报告 1、碳资产 1、培训 1）

# 3) 幂等验证：对 clean.csv 再跑一次，去重数应为 0
python -I scripts/normalize_dedup.py \
  --input /tmp/tender-demo/clean.csv --outdir /tmp/tender-demo/rerun
```

> Windows 下 `/tmp` 可替换为任意可写目录（如 `./.demo`），演示后可删除。

## 演示：月报校验

```bash
cd skills/esg-monthly-brief

# 示例应全部 PASS（退出码 0）
python -I scripts/check_brief.py assets/example-brief.md

# 负例：删掉“行业实践”一节后应 FAIL（退出码 1）
python -I scripts/check_brief.py <截断后的文件>
```

脚本校验：四个章节、每条政策的传导判断、案例四要素、张力证据、同行对标表、
≥3 个可证伪跟踪点、来源链接数、占位符。**数据真实性无法机器校验**，仍需人工回溯来源。

## 质量门与验证方式

### esg-monthly-brief

| 质量门 | 验证方式 |
| --- | --- |
| `transmission_judgment_present` | 检查每条政策是否含“变化→机制→影响”；脚本校验 |
| `peer_cases_decomposable` | 检查案例四要素；脚本校验 |
| `tension_identified_or_explicitly_cleared` | 有证据的张力，或显式声明无张力及依据；脚本 + 人工 |
| `sources_cited` | 链接数 ≥5 且“来源”标注 ≥3；脚本校验 |
| `no_fabricated_data` | 无占位符；真实性人工核对 |
| `tracking_points_falsifiable` | ≥3 个跟踪点，各含时间/信号/验证方式/判定；脚本校验 |

### esg-tender-intel

| 质量门 | 验证方式 |
| --- | --- |
| `keyword_scope_disclosed` | 脚本首行输出词库层级、版本、阈值 |
| `dedup_reproducible` | `dedup_report.json` 可复算输入数/去重数/重复率 |
| `amount_basis_unified` | 金额统一万元，`amount_basis` 与覆盖率写入报告 |
| `service_tags_complete` | `tagged.csv` 主标签无空值，`other` 单列 |
| `coverage_disclaimer_present` | 报告开头“数据覆盖声明”模板 |
| `push_noise_filtered` | 低于阈值/排除词/仅 L3 命中均标记噪声 |
| `no_fabricated_data` | 样例文件内显著标注“虚构”；真实任务要求回源核对 |

## 设计要点

1. **渐进式披露**：`SKILL.md` 只写流程与判断规则，细则按需加载 `references/`；
2. **确定性交给脚本**：去重、金额换算、打标、结构校验均以标准库 Python 实现，可重复运行；
3. **禁止编造**：无法联网/无数据源时必须显式声明缺口，不得用记忆生成带日期与数字的结论；
4. **质量门前置**：每个技能在 frontmatter 中声明 gate，正文给出逐条判定与验证方式。

## 环境与依赖

- Python 3.10+（开发环境 3.12），仅使用标准库，无第三方依赖；
- 脚本统一输出 UTF-8，CSV 使用 `utf-8-sig` 兼容 Excel；
- 建议以 `python -I` 隔离模式运行，避免当前目录的模块干扰。

## 说明

- 两个 `SKILL.md` 的 frontmatter 保留了作业设计的自定义字段（`category`、`trigger_phrases`、
  `inputs_required`、`output_structure`、`quality_gates`、`modes`、`shared_rules`）。
  官方规范仅强制 `name` 与 `description`；若需上传到严格校验的环境，可将自定义字段整体移入 `metadata:`。
- `assets/example-brief.md` 与 `assets/sample-tenders-raw.csv` 均为**虚构示例**，
  公司、政策、金额、链接均为演示占位，不得作为真实信息引用。
