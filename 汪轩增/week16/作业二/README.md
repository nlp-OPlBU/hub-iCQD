# 作业二：Script 是什么、怎么用

`script`（脚本）就是一段能让电脑按固定步骤执行的程序。此作业中的 [`esg_pipeline.py`](scripts/esg_pipeline.py) 接收 JSON 格式的公告清单，做标准化、去重、主题词初筛、金额换算和 Markdown 汇总。它不负责联网搜索，也不能代替人判断项目是否真正属于 ESG。

## 输入和输出

- 输入：[`data/tender_sample.json`](data/tender_sample.json)，每项一条已搜索并初步核实的公开公告。
- 输出：[`results/script_output.md`](results/script_output.md)，去重明细及主题、类别、地区和金额统计。
- 实验说明：[`对照实验记录.md`](对照实验记录.md)。

## 如何运行

在工作区根目录执行：

```powershell
python 作业二/scripts/esg_pipeline.py 作业二/data/tender_sample.json -o 作业二/results/script_output.md
```

脚本使用 Python 标准库，无需额外安装第三方包。主题词在脚本开头 `TERMS` 中维护；新增关键词后仍需人工检查误判和漏判。
