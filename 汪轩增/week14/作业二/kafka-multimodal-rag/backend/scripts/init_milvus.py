"""手动创建 Milvus collection / 检查 schema。

    cd backend
    python scripts/init_milvus.py

后端和 worker 运行时也会自动建表，这个脚本主要用于提前验证 Milvus 连通性。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402


def main() -> None:
    settings = get_settings()
    store = VectorStore(settings)
    store.ensure_collection()
    print(f"Milvus collection ready: {settings.milvus_collection}")
    print(f"  uri = {settings.milvus_uri}")
    print(f"  text dim = {settings.bge_dim}")
    print(f"  clip dim = {settings.clip_dim}")


if __name__ == "__main__":
    main()
