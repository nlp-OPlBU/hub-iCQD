#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Week13 作业二 CLI 入口（与 API 复用同一份 sql_agent_lg）。

    python run.py "总共有多少张专辑？"
    python run.py --mock "总共有多少张专辑？"

Web 启动方式见 README：python -m uvicorn server.main:app --port 8000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sql_agent_lg.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
