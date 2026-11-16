# -*- coding: utf-8 -*-
"""集中配置：从项目根目录 .env 读取密钥与运行参数。

路径统一基于本文件定位（Path(__file__).resolve().parent），
保证无论从哪个工作目录启动（uvicorn backend.app:app 或 python -m）都能生效。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录
BASE_DIR = Path(__file__).resolve().parent
# 研究报告落盘目录
DATA_DIR = BASE_DIR / "data" / "research"
# Jinja2 提示词模板目录
TEMPLATE_DIR = BASE_DIR / "templates"

# 加载项目根目录 .env（密钥不提交源码）
load_dotenv(BASE_DIR.parent / ".env")

# --- LLM（DeepSeek，OpenAI 兼容）---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/")
MODEL_NAME = os.environ.get("MODEL_NAME", "deepseek-v4-flash")

# --- Bocha 网页搜索 ---
BOCHA_API_KEY = os.environ.get("BOCHA_API_KEY", "")
BOCHA_SEARCH_COUNT = int(os.environ.get("BOCHA_SEARCH_COUNT", "10"))

# --- 研究循环（检索/判断轮数上限）---
MAX_ROUNDS = int(os.environ.get("RESEARCH_MAX_ROUNDS", "3"))

# --- LLM 调用 ---
LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "3"))  # 空输出时重试次数（DeepSeek 偶发 200+空 content）


def ensure_data_dir() -> None:
    """幂等地创建研究报告落盘目录。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def today_str() -> str:
    """返回今天的日期字符串（YYYY-MM-DD，本地时区），用于提示词中的信息截止时间。"""
    from datetime import date

    return date.today().isoformat()


if __name__ == "__main__":
    # 测试 demo：打印当前生效的配置并校验目录就绪（无需网络/密钥）
    print("BASE_DIR     =", BASE_DIR)
    print("DATA_DIR     =", DATA_DIR)
    print("TEMPLATE_DIR =", TEMPLATE_DIR)
    print("MODEL_NAME   =", MODEL_NAME)
    print("OPENAI_BASE_URL =", OPENAI_BASE_URL)
    print("BOCHA_SEARCH_COUNT =", BOCHA_SEARCH_COUNT)
    print("MAX_ROUNDS   =", MAX_ROUNDS)
    print("LLM_RETRIES  =", LLM_RETRIES)
    print("today_str()  =", today_str())
    ensure_data_dir()
    print("DATA_DIR 已创建:", DATA_DIR.exists())
    print("配置自检 OK")
