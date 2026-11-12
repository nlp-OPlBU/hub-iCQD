"""Minimal Elasticsearch HTTP adapter for versioned chunk indexing."""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request

from app.core.config import Settings


class ElasticsearchError(Exception):
    pass


class ElasticsearchStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str = "application/json") -> tuple[int, bytes]:
        url = self.settings.es_url.rstrip("/") + path
        headers = {"Content-Type": content_type}
        if self.settings.es_username:
            token = f"{self.settings.es_username}:{self.settings.es_password.get_secret_value()}"
            headers["Authorization"] = "Basic " + base64.b64encode(token.encode()).decode()
        context = None
        if url.startswith("https://") and self.settings.es_ca_certs:
            context = ssl.create_default_context(cafile=str(self.settings.es_ca_certs))
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ElasticsearchError("Elasticsearch unavailable") from exc

    def ensure_index(self) -> None:
        status, _ = self._request("HEAD", f"/{self.settings.es_index}")
        if status == 200:
            return
        mapping = {
            "mappings": {"properties": {
                "chunk_id": {"type": "keyword"}, "document_id": {"type": "keyword"},
                "version": {"type": "integer"}, "filename": {"type": "keyword"},
                "page": {"type": "integer"}, "chunk_index": {"type": "integer"},
                "text": {"type": "text", "analyzer": self.settings.es_analyzer},
            }}
        }
        status, _ = self._request("PUT", f"/{self.settings.es_index}",
                                  json.dumps(mapping).encode())
        if status not in (200, 400):  # 400 includes a concurrent already-exists create.
            raise ElasticsearchError("Index creation failed")

    def index_chunks(self, chunks: list[dict]) -> None:
        if not chunks:
            return
        self.ensure_index()
        lines: list[str] = []
        for chunk in chunks:
            lines.append(json.dumps({"index": {"_index": self.settings.es_index,
                                               "_id": chunk["chunk_id"]}}, ensure_ascii=False))
            lines.append(json.dumps(chunk, ensure_ascii=False))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        status, _ = self._request("POST", "/_bulk?refresh=true", body,
                                  content_type="application/x-ndjson")
        if status != 200:
            raise ElasticsearchError("Bulk indexing failed")
