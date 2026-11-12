from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 读取项目根目录 .env（不存在时静默跳过）


@dataclass
class Config:
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_reasoning_effort: str | None = "max"
    bocha_api_key: str | None = None
    bocha_base_url: str = "https://api.bocha.cn/v1/web-search"
    max_rounds: int = 2
    max_total_queries: int = 30
    output_dir: Path = Path("outputs")


def load_config() -> Config:
    return Config(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        deepseek_reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "max"),
        bocha_api_key=os.getenv("BOCHA_API_KEY"),
        bocha_base_url=os.getenv("BOCHA_BASE_URL", "https://api.bocha.cn/v1/web-search"),
        max_rounds=int(os.getenv("DEEP_RESEARCH_MAX_ROUNDS", "2")),
        max_total_queries=int(os.getenv("DEEP_RESEARCH_MAX_TOTAL_QUERIES", "30")),
        output_dir=Path(os.getenv("DEEP_RESEARCH_OUTPUT_DIR", "outputs")),
    )
