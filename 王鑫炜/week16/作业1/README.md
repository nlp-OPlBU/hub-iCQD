# 作业1 · 两个 ESG Skill

| Skill | 任务 | 示例交付 |
| --- | --- | --- |
| [esg-monthly-report](skills/esg-monthly-report/SKILL.md) | 从监管、评级、行业、公司和同业来源自主检索、核验并形成企业 ESG 月报 | [中国神华 ESG 月报](报告/中国神华ESG月报.md) |
| [esg-procurement-watch](skills/esg-procurement-watch/SKILL.md) | 统计近两年 ESG 服务采购的中标样本，并生成每日新增招标推送 | [中标样本分析](报告/ESG招投标两年回顾.md) · [新增招标推送样例](报告/ESG招标推送样例.md) |

在支持 Agent Skills 与联网检索的环境中，注册整个 Skill 目录，保留其相对引用。也可以在支持联网的会话中直接提供对应的 `SKILL.md` 及其 `references/`。

- 月报调用示例 ·「用 `$esg-monthly-report` 为中国神华生成 2026 年 10 月的 ESG 月报，核对政策、评级、行业实践及上市公司公告原文，并按优先级提出建议。」
- 样本分析调用示例 ·「用 `$esg-procurement-watch` 汇总截至今天近两年的中国大陆 ESG、可持续发展和双碳相关服务中标。请给出样本口径、主题频次、金额分布及原始公告。」
- 日报调用示例 ·「用 `$esg-procurement-watch` 查询上次成功检查到今天之间的新公告，核验报名和投标状态，输出去重后的每日推送稿。」

日报示例是一次历史截面的生成结果。持续推送需在使用环境配置每日调度、持久化状态及接收渠道。部署方式见招投标 Skill 的说明。
