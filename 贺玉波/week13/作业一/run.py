#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Week13 作业一 直接运行入口（LangGraph 版 SQL Agent）。

    python run.py "总共有多少张专辑？"
    python run.py --mock "总共有多少张专辑？"
    python run.py --mock --scenario repair "总共有多少张专辑？"
    python run.py --sql "SELECT COUNT(*) AS album_count FROM Album;" --mock

LLM 配置见同目录 .env（DEEPSEEK_API_KEY），数据库为 data/chinook.db。
"""

import sys
from pathlib import Path

# 让脚本无论从哪个目录启动都能找到 sql_agent_lg 包。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sql_agent_lg.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
