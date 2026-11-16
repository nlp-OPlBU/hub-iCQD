"""进程入口：``python -m app``。

显式 ``log_config=None``，避免 uvicorn 覆盖项目的 JSON 日志配置。
"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.core.logging import setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
