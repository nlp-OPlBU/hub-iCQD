"""两项作业共用配置。密钥只在后端读取，不写入前端或提交文件。"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    base_url: str = ""
    vision_model: str = "qwen3-vl-plus"
    sql_model: str = "qwen-plus"
    timeout: float = 60.0
    disable_thinking: bool = True
    vision_json_mode: bool = False

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            api_key=(os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")).strip(),
            base_url=os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/"),
            vision_model=os.getenv("VISION_MODEL", "qwen3-vl-plus").strip(),
            sql_model=os.getenv("SQL_MODEL", "qwen-plus").strip(),
            timeout=float(os.getenv("MODEL_TIMEOUT", "60")),
            disable_thinking=os.getenv("QWEN_DISABLE_THINKING", "true").lower() == "true",
            vision_json_mode=os.getenv("VISION_JSON_MODE", "false").lower() == "true",
        )

    def validate_online(self):
        if not self.api_key or not self.base_url or "{" in self.base_url:
            raise ValueError("请在项目 .env 中配置有效的 DASHSCOPE_API_KEY 和 OPENAI_BASE_URL。")
        parts = urlsplit(self.base_url)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parts.hostname or parts.scheme not in {"https", "http"}
                or (parts.scheme == "http" and not local) or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError("模型地址须为 HTTPS 的兼容接口；HTTP 仅允许本机测试。")
        if not self.vision_model or not self.sql_model or not 1 <= self.timeout <= 180:
            raise ValueError("模型名不能为空，MODEL_TIMEOUT 必须在 1～180 秒内。")
