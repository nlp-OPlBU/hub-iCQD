# -*- coding: utf-8 -*-
"""多 agent 包：只含「角色」agent，不含编排逻辑。

- base      所有角色的基类（以 base model 为基础调用 + JSON 解析）
- keyword   生成搜索关键词的 agent
- summary   对搜索结果做总结、抽取事实的 agent
- judge     判断信息是否足够、并生成补充关键词的 agent
- report    生成结构化报告 + HTML 报告的 agent

编排（把这些 agent 组合成研究流程的引擎）见 backend/engine.py 的 DeepResearch。
"""
from .base import BaseAgent
from .judge import JudgeAgent
from .keyword import KeywordAgent
from .report import ReportAgent
from .summary import SummaryAgent

__all__ = ["BaseAgent", "KeywordAgent", "SummaryAgent", "JudgeAgent", "ReportAgent"]
