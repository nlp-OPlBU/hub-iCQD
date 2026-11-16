"""测试夹具：全部使用真实中间件，靠独立库 / topic / 目录隔离，不使用 mock。

- MySQL：``rag_test`` 库，测试前建表、按需清表
- Redis：db=1，每个用例 flushdb
- Kafka：``doc.ingest.test`` / ``doc.ingest.test.dlq``
- Milvus：``data/_test/milvus.db``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("APP_ENV", "test")
os.environ["MYSQL__DATABASE"] = "rag_test"
os.environ["REDIS__DB"] = "1"
os.environ["KAFKA__TOPIC"] = "doc.ingest.test"
os.environ["KAFKA__DLQ_TOPIC"] = "doc.ingest.test.dlq"
os.environ["KAFKA__GROUP_ID"] = "rag-ingest-worker-test"
os.environ["MILVUS__URI"] = "data/_test/milvus.db"
os.environ["STORAGE__RAW_DIR"] = "data/_test/raw"
os.environ["STORAGE__PARSED_DIR"] = "data/_test/parsed"
os.environ["STORAGE__IMAGE_DIR"] = "data/_test/images"
os.environ["TRACE__PERSIST"] = "true"
# 短问题改写会真调一次外部模型；测试必须在模型边界内，不能连真实云端账单
os.environ["RETRIEVAL__REWRITE_ENABLED"] = "false"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepare_dirs() -> None:
    from app.core.config import get_settings

    get_settings().ensure_dirs()


@pytest.fixture(scope="session")
def mysql_schema():
    """建 rag_test 库并执行 sql/*.sql；返回已绑定测试库的 engine。"""
    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    settings = get_settings().mysql
    admin = create_engine(settings.server_dsn, future=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        # DDL 里全是 CREATE TABLE IF NOT EXISTS，残留的旧表会掩盖表结构变更；
        # 只在 *_test 库上重建，避免误连生产库时把真数据删掉
        if settings.database.endswith("_test"):
            conn.execute(text(f"DROP DATABASE IF EXISTS `{settings.database}`"))
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    admin.dispose()

    engine = create_engine(settings.dsn(), future=True)
    for sql_file in sorted((BACKEND / "sql").glob("*.sql")):
        with engine.begin() as conn:
            for statement in sql_file.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    conn.execute(text(statement))
    yield engine
    engine.dispose()


@pytest.fixture()
def mysql_clean(mysql_schema):
    """清空会话与链路表，保证用例之间不互相污染。"""
    from sqlalchemy import text

    def truncate() -> None:
        with mysql_schema.begin() as conn:
            for table in ("chat_message", "chat_session", "trace_span"):
                conn.execute(text(f"TRUNCATE TABLE `{table}`"))

    truncate()
    yield mysql_schema
    truncate()


@pytest.fixture()
def redis_clean():
    from app.infra.cache.redis_client import get_redis

    client = get_redis()
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture(scope="session")
def kafka_topics() -> dict[str, str]:
    from app.infra.mq.kafka_client import ensure_topics

    return ensure_topics()


@pytest.fixture(scope="session")
def milvus_ready() -> list[str]:
    from app.infra.vector.milvus_client import ensure_collections

    return ensure_collections()
