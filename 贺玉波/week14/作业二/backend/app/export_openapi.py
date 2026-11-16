"""导出 OpenAPI 快照：``python -m app.export_openapi [输出路径]``。

接口一旦变更，快照会跟着变——把它纳入版本管理后，
前端类型定义、演示文档和接口 diff 都有唯一可引用的基线。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core.config import PROJECT_ROOT


def main() -> None:
    from app.main import create_app

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "docs" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"openapi 快照已写入 {target}（{len(schema.get('paths', {}))} 条路径）")


if __name__ == "__main__":
    main()
