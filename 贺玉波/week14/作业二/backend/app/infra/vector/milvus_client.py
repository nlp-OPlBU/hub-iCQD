"""Milvus Lite：单文件向量库，两个集合（文本 / 图像）。

写入策略见 plan §6.3：先按 doc_id 删除再插入，保证重放不产生重复向量。
"""

from __future__ import annotations

import logging
from typing import Any

from pymilvus import DataType, MilvusClient

from app.core.config import get_settings
from app.core.logging import get_logger, log_event

logger = get_logger("app.infra.milvus")

_client: MilvusClient | None = None


def get_milvus() -> MilvusClient:
    global _client
    if _client is None:
        settings = get_settings().milvus
        # 本地文件模式是单进程独占；部署时用 `milvus-lite server` 起独立服务，
        # api 与 worker 才能共享同一份向量库（否则 worker 写入会拿不到文件锁）。
        if not settings.is_remote:
            settings.path.parent.mkdir(parents=True, exist_ok=True)
        _client = MilvusClient(uri=settings.connect_uri)
    return _client


def _text_schema(dim: int) -> Any:
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=32)
    schema.add_field("kind", DataType.VARCHAR, max_length=16)
    schema.add_field("page_no", DataType.INT64)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    return schema


def _image_schema(dim: int) -> Any:
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=32)
    schema.add_field("image_path", DataType.VARCHAR, max_length=512)
    schema.add_field("caption", DataType.VARCHAR, max_length=1024)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    return schema


def ensure_collections() -> list[str]:
    """幂等建集合与索引，返回已就绪集合名。"""
    settings = get_settings().milvus
    client = get_milvus()
    specs = [
        (settings.text_collection, _text_schema(settings.text_dim)),
        (settings.image_collection, _image_schema(settings.image_dim)),
    ]
    ready: list[str] = []
    for name, schema in specs:
        if not client.has_collection(name):
            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type=settings.index_type,
                metric_type=settings.metric_type,
                params={},
            )
            client.create_collection(collection_name=name, schema=schema, index_params=index_params)
            log_event(logger, "milvus_collection_created", collection=name)
        # Milvus Lite 新建集合默认未加载，检索前必须 load
        client.load_collection(name)
        ready.append(name)
    return ready


def ping_milvus() -> int:
    return len(get_milvus().list_collections())


# ---------------------------------------------------------------- 写入

_loaded: set[str] = set()


def _ensure_loaded(name: str) -> None:
    """Lite 实例每个进程都要 load 一次才能检索，缓存避免重复往返。"""
    if name in _loaded:
        return
    get_milvus().load_collection(name)
    _loaded.add(name)


def delete_doc_vectors(doc_id: str) -> None:
    """按文档清空两个集合里的旧向量，保证重放幂等。"""
    settings = get_settings().milvus
    client = get_milvus()
    for name in (settings.text_collection, settings.image_collection):
        client.delete(collection_name=name, filter=f'doc_id == "{doc_id}"')


def upsert_text_vectors(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    name = get_settings().milvus.text_collection
    _ensure_loaded(name)
    get_milvus().upsert(collection_name=name, data=rows)
    return len(rows)


def upsert_image_vectors(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    name = get_settings().milvus.image_collection
    _ensure_loaded(name)
    get_milvus().upsert(collection_name=name, data=rows)
    return len(rows)


# ---------------------------------------------------------------- 检索


def _hits(raw: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for group in raw:
        for hit in group:
            entity = dict(hit.get("entity") or {})
            # pymilvus 不同版本主键键名不一致：标准是 id，Lite 引擎直接回 chunk_id
            entity["chunk_id"] = hit.get("id") or hit.get("chunk_id") or entity.get("chunk_id")
            entity["score"] = float(hit.get("distance") or 0.0)
            results.append(entity)
    return results


def search_text(vector: list[float], top_k: int, doc_id: str | None = None) -> list[dict[str, Any]]:
    settings = get_settings().milvus
    _ensure_loaded(settings.text_collection)
    raw = get_milvus().search(
        collection_name=settings.text_collection,
        data=[vector],
        limit=top_k,
        filter=f'doc_id == "{doc_id}"' if doc_id else "",
        output_fields=["doc_id", "kind", "page_no", "text"],
    )
    return _hits(raw)


def search_image(vector: list[float], top_k: int, doc_id: str | None = None) -> list[dict[str, Any]]:
    settings = get_settings().milvus
    _ensure_loaded(settings.image_collection)
    raw = get_milvus().search(
        collection_name=settings.image_collection,
        data=[vector],
        limit=top_k,
        filter=f'doc_id == "{doc_id}"' if doc_id else "",
        output_fields=["doc_id", "image_path", "caption"],
    )
    return _hits(raw)


def count_vectors(doc_id: str | None = None) -> tuple[int, int]:
    """统计两集合行数，用于索引校验与测试断言。"""
    settings = get_settings().milvus
    client = get_milvus()
    expression = f'doc_id == "{doc_id}"' if doc_id else ""
    counts = []
    for name in (settings.text_collection, settings.image_collection):
        rows = client.query(collection_name=name, filter=expression or 'chunk_id != ""', output_fields=["count(*)"])
        counts.append(int(rows[0]["count(*)"]) if rows else 0)
    return counts[0], counts[1]


def close_milvus() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception as exc:
            logger.warning("milvus close failed: %s", exc)
    _client = None
    _loaded.clear()
