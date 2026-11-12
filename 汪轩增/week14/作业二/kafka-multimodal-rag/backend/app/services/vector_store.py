"""Milvus 封装：一个 collection 同时存文本向量、CLIP 文本向量、CLIP 图片向量。"""

import logging
import threading

from pymilvus import DataType, MilvusClient

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

OUTPUT_FIELDS = ["text", "doc_id", "file_name", "image_paths", "chunk_index"]


class VectorStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: MilvusClient | None = None
        self._ready = False
        self._lock = threading.Lock()

    # ---------------- 连接 / 建表 ----------------
    def _get_client(self) -> MilvusClient:
        if self._client is None:
            kwargs = {"uri": self.settings.milvus_uri}
            if self.settings.milvus_token:
                kwargs["token"] = self.settings.milvus_token
            self._client = MilvusClient(**kwargs)
        return self._client

    def ensure_collection(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return

            client = self._get_client()
            name = self.settings.milvus_collection

            if client.has_collection(name):
                self._check_schema(client, name)
                self._try_load(client, name)
                self._ready = True
                logger.info("Milvus collection ready: %s", name)
                return

            schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("doc_id", DataType.INT64)
            schema.add_field("chunk_index", DataType.INT64)
            schema.add_field("file_name", DataType.VARCHAR, max_length=512)
            schema.add_field("text", DataType.VARCHAR, max_length=65535)
            schema.add_field("image_paths", DataType.JSON)
            schema.add_field("text_vector", DataType.FLOAT_VECTOR, dim=self.settings.bge_dim)
            schema.add_field(
                "clip_text_vector", DataType.FLOAT_VECTOR, dim=self.settings.clip_dim
            )
            schema.add_field(
                "clip_image_vector", DataType.FLOAT_VECTOR, dim=self.settings.clip_dim
            )

            index_params = MilvusClient.prepare_index_params()
            for field in ("text_vector", "clip_text_vector", "clip_image_vector"):
                # embedding 已归一化，用内积 IP 等价于余弦相似度
                index_params.add_index(
                    field_name=field, index_type="AUTOINDEX", metric_type="IP"
                )

            client.create_collection(
                collection_name=name, schema=schema, index_params=index_params
            )
            self._try_load(client, name)
            self._ready = True
            logger.info("Milvus collection created: %s", name)

    def _check_schema(self, client: MilvusClient, name: str) -> None:
        try:
            info = client.describe_collection(name)
            existing = {field.get("name") for field in info.get("fields", [])}
        except Exception:
            logger.warning("无法读取 collection schema，跳过校验: %s", name)
            return

        required = {
            "doc_id",
            "chunk_index",
            "file_name",
            "text",
            "image_paths",
            "text_vector",
            "clip_text_vector",
            "clip_image_vector",
        }
        missing = required - existing
        if missing:
            raise RuntimeError(
                f"Collection {name} 缺少字段 {sorted(missing)}，"
                "请更换 MILVUS_COLLECTION 名称或删除旧 collection 后重建"
            )

    @staticmethod
    def _try_load(client: MilvusClient, name: str) -> None:
        try:
            client.load_collection(name)
        except Exception:
            # 已加载时会报错，忽略即可
            pass

    # ---------------- 写入 ----------------
    def insert(self, records: list[dict], batch_size: int = 64) -> int:
        self.ensure_collection()
        client = self._get_client()
        total = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            result = client.insert(collection_name=self.settings.milvus_collection, data=batch)
            total += int(result.get("insert_count", len(batch)))
        return total

    def delete_by_doc(self, document_id: int) -> None:
        self.ensure_collection()
        self._get_client().delete(
            collection_name=self.settings.milvus_collection,
            filter=f"doc_id == {int(document_id)}",
        )

    # ---------------- 检索 ----------------
    def search(
        self,
        vector: list[float],
        anns_field: str,
        limit: int,
        doc_ids: list[int] | None = None,
    ) -> list[dict]:
        self.ensure_collection()
        filter_expr = ""
        if doc_ids:
            ids = ", ".join(str(int(i)) for i in doc_ids)
            filter_expr = f"doc_id in [{ids}]"

        result = self._get_client().search(
            collection_name=self.settings.milvus_collection,
            data=[vector],
            anns_field=anns_field,
            limit=max(limit, 1),
            filter=filter_expr,
            output_fields=OUTPUT_FIELDS,
            search_params={"metric_type": "IP"},
        )

        hits: list[dict] = []
        if not result:
            return hits
        for hit in result[0]:
            hits.append(
                {
                    "chunk_id": hit.get("id"),
                    "score": float(hit.get("distance", 0.0)),
                    "entity": hit.get("entity", {}) or {},
                }
            )
        return hits
