# 去重与金额口径（dedup-and-amount）

## 一、项目编号归一化

同一项目在不同平台的项目编号常有差异（大小写、分隔符、全半角）。归一化步骤：

1. Unicode NFKC 归一（全角转半角）；
2. 去首尾空白；
3. 转大写；
4. 删除分隔符与空白：`空格 - _ / \ （） () 【】 [] 、`；
5. 结果作为 `dedup_key`。

示例：

| 原始编号 | 归一化 |
| --- | --- |
| `ZB-2025-001` | `ZB2025001` |
| `zb 2025 001` | `ZB2025001` |
| `ＺＢ－２０２５－００１` | `ZB2025001` |

## 二、无编号时的组合键

项目编号缺失（为空或 `-`）时，使用：

```text
dedup_key = "T|" + norm(title) + "|" + norm(buyer) + "|" + publish_date[:7]
```

- `norm()`：NFKC、去空白、小写；
- `publish_date[:7]`：取 `YYYY-MM`，避免同日不同写法导致漏合并；
- 报告中必须注明“其中 N 条使用组合键去重”，提示存在误合并风险。

## 三、重复组保留策略

对同一 `dedup_key` 的多条记录，按下述顺序打分，保留得分最高者：

| 因素 | 分值 |
| --- | --- |
| 每个非空字段 | +1 |
| 含中标金额 | +2 |
| 含供应商名称 | +1 |
| 来源为官方平台（source_url 含 gov） | +1 |

- 得分相同保留首次出现的记录（稳定排序，保证可复现）；
- 被丢弃记录的编号、来源写入 `dedup_report.json` 的 `duplicate_groups`，便于审计。

## 四、金额口径统一

**统一单位：万元**，保留 2 位小数。识别规则：

| 原始写法 | 识别单位 | 换算 |
| --- | --- | --- |
| `120万元` / `120万` | 万元 | ×1 |
| `1,200,000元` / `1200000` | 元 | ÷10000 |
| `0.05亿元` / `1.2亿` | 亿元 | ×10000 |
| 纯数字无单位 | 元（保守假设） | ÷10000，并标记 `unit_inferred` |

口径优先级（决定 `amount_basis`）：

```text
中标金额 > 合同金额 > 预算金额
```

- 同一记录同时有多个口径时，取优先级最高者进入 `amount_wan`，其余保留原始字段；
- 全部缺失时 `amount_wan` 为空、`amount_basis = unknown`；
- 报告金额统计只使用 `amount_wan` 非空的记录，并披露“已知金额覆盖率 = 非空数 / 去重项目数”。

## 五、去重报告字段

`dedup_report.json` 至少包含：

```json
{
  "total_in": 16,
  "unique_out": 13,
  "duplicate_rows_removed": 3,
  "duplicate_rate": 0.1875,
  "duplicate_groups": [
    {"dedup_key": "ZB2025001", "kept": "ZB-2025-001", "dropped": ["zb 2025 001"], "reason": "completeness_score"}
  ],
  "amount_stats": {
    "known": 11, "unknown": 2, "coverage": 0.8462,
    "total_wan": 1234.5, "min_wan": 18.0, "max_wan": 800.0, "median_wan": 68.0
  },
  "amount_basis_counts": {"award": 9, "budget": 2, "unknown": 2},
  "unit_inferred_count": 1,
  "composite_key_count": 0,
  "notes": ["金额统一为万元；纯数字按元处理"]
}
```

## 六、可复现与幂等

- 输入排序不影响结果：去重前按（`dedup_key`, 原始行号）稳定排序；
- 对已清洗文件再次运行，`amount_wan` 与 `dedup_key` 保持不变（脚本优先读取已存在的 `amount_wan` 列），
  `unique_out` 不变，重复率为 0；
- 报告中的计数必须能由输入文件复算得到。
