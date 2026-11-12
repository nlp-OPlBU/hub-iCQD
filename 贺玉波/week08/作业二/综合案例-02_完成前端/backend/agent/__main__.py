# -*- coding: utf-8 -*-
"""agent 包的测试 demo：`python3 -m backend.agent` 时运行（纯本地，不调用 LLM/网络）。

验证包内各角色 agent 均可实例化、模板名已配置；编排器 DeepResearch 在 backend/engine.py。
"""
from . import __all__
from .judge import JudgeAgent
from .keyword import KeywordAgent
from .report import ReportAgent
from .summary import SummaryAgent

if __name__ == "__main__":
    agents = [KeywordAgent(), SummaryAgent(), JudgeAgent(), ReportAgent()]
    print("backend.agent 包导入 OK，导出:", ", ".join(__all__))
    for a in agents:
        print(" -", type(a).__name__, "| template:", a.template_name)
