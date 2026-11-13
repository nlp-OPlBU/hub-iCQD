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
            with urllib.request.urlopen(request, timeout=self.settings.es_timeout_seconds, context=context) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ElasticsearchError("Elasticsearch unavailable") from exc

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        status, body = self._request(method, path, json.dumps(payload).encode() if payload is not None else None)
        if status not in (200, 201):
            raise ElasticsearchError(f"Elasticsearch request failed: HTTP {status}")
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError) as exc:
            raise ElasticsearchError("Invalid Elasticsearch response") from exc

    def ensure_index(self) -> None:
        status, _ = self._request("HEAD", f"/{self.settings.es_index}")
        if status == 200:
            return
        if status != 404:
            raise ElasticsearchError(f"Index check failed: HTTP {status}")
        mapping = {"mappings": {"properties": {
            "chunk_id": {"type": "keyword"}, "document_id": {"type": "keyword"},
            "version": {"type": "integer"}, "filename": {"type": "keyword"},
            "page": {"type": "integer"}, "chunk_index": {"type": "integer"},
            "block_index": {"type": "integer"}, "char_offset": {"type": "integer"},
            "bbox": {"type": "float", "index": False},
            "image_keys": {"type": "keyword"}, "page_image_keys": {"type": "keyword"},
            "text": {"type": "text", "analyzer": self.settings.es_analyzer},
        }}}
        status, body = self._request("PUT", f"/{self.settings.es_index}", json.dumps(mapping).encode())
        if status == 200:
            return
        try:
            concurrent = status == 400 and json.loads(body)['error']['type'] == 'resource_already_exists_exception'
        except (ValueError, KeyError, TypeError):
            concurrent = False
        if not concurrent:
            raise ElasticsearchError(f"Index creation failed: HTTP {status}")

    def index_chunks(self, chunks: list[dict]) -> None:
        if not chunks:
            raise ElasticsearchError("No searchable chunks")
        self.ensure_index()
        for start in range(0, len(chunks), self.settings.es_bulk_batch_size):
            batch = chunks[start:start + self.settings.es_bulk_batch_size]
            lines = []
            for chunk in batch:
                lines.append(json.dumps({"index": {"_index": self.settings.es_index, "_id": chunk["chunk_id"]}}))
                lines.append(json.dumps(chunk, ensure_ascii=False))
            status, body = self._request("POST", "/_bulk?refresh=true", ("\n".join(lines) + "\n").encode(),
                                         content_type="application/x-ndjson")
            try:
                result = json.loads(body)
                items = result['items']
                valid = (status == 200 and result.get('errors') is False and len(items) == len(batch)
                         and all(200 <= item['index']['status'] < 300 and not item['index'].get('error') for item in items))
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                raise ElasticsearchError("Bulk indexing failed (HTTP or item error)")
            # Verify visibility through _search, rather than trusting a successful write.
            visible = self._json('POST', f'/{self.settings.es_index}/_search', {
                'size': 0, 'track_total_hits': True,
                'query': {'ids': {'values': [c['chunk_id'] for c in batch]}},
            })
            if (visible.get('timed_out') or visible.get('_shards', {}).get('failed', 0)
                    or visible.get('hits', {}).get('total', {}).get('value') != len(batch)):
                raise ElasticsearchError('Indexed chunks are not searchable')

    def search(self, query: str, *, versions: dict[str, int], limit: int = 10) -> list[dict]:
        """Caller supplies only MySQL-completed current versions, intersected with user scope."""
        if not versions or not query.strip():
            return []
        scope = [{'bool': {'filter': [{'term': {'document_id': doc}}, {'term': {'version': version}}]}}
                 for doc, version in versions.items()]
        result = self._json('POST', f'/{self.settings.es_index}/_search', {
            'size': max(1, min(limit, 100)), 'query': {'bool': {
                'must': [{'match': {'text': query}}],
                'filter': [{'bool': {'should': scope, 'minimum_should_match': 1}}],
            }},
        })
        if result.get('timed_out') or result.get('_shards', {}).get('failed', 0):
            raise ElasticsearchError('Incomplete search response')
        try:
            return [dict(hit['_source'], score=hit['_score']) for hit in result['hits']['hits']]
        except (KeyError, TypeError) as exc:
            raise ElasticsearchError('Invalid search response') from exc
