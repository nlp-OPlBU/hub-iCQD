#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Week12 作业二 直接运行入口（LangChain 版 SQL Agent）。

等价于 `python -m sql_agent_lc`，区别只是可以像普通 py 文件一样直接执行：

    python run.py "总共有多少张专辑？"
    python run.py --log-level DEBUG "销售额最高的前 5 位客户是谁？"
    python run.py                       # 交互输入问题

LLM 配置见同目录 .env（DEEPSEEK_API_KEY），数据库为 data/chinook.db。
"""

import sys
from pathlib import Path

# 让脚本无论从哪个目录启动都能找到 sql_agent_lc 包。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sql_agent_lc.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
