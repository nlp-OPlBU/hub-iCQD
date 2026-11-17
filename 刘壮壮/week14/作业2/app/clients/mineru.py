"""MinerU v4 local-file batch upload and polling client."""
import asyncio
from pathlib import Path

import httpx

from app.config import get_settings


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    def __init__(self):
        self.settings = get_settings()
        if not self.settings.mineru_api_url or not self.settings.mineru_api_key:
            raise MinerUError("RAG_MINERU_API_URL and RAG_MINERU_API_KEY are required")
        self.base_url = self.settings.mineru_api_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.settings.mineru_api_key}"}

    async def submit_local_pdf(self, path: Path, data_id: str) -> str:
        timeout = httpx.Timeout(self.settings.mineru_upload_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{self.base_url}/file-urls/batch", headers=self.headers, json={
                "files": [{"name": path.name, "data_id": data_id}],
                "model_version": self.settings.mineru_model_version,
            })
            body = self._body(response)
            if body.get("code") != 0:
                raise MinerUError(f"MinerU URL request rejected: {body.get('msg', 'unknown error')}")
            data = body.get("data") or {}
            batch_id, urls = data.get("batch_id"), data.get("file_urls") or []
            if not batch_id or not urls:
                raise MinerUError("MinerU response did not include batch_id and upload URL")
            # The signed object URL expects an unauthenticated PUT without Content-Type.
            async def file_chunks():
                # Stream larger PDFs without loading the complete file into memory.
                with path.open("rb") as pdf:
                    while chunk := await asyncio.to_thread(pdf.read, 1024 * 1024):
                        yield chunk
            uploaded = await client.put(urls[0], content=file_chunks(), timeout=timeout)
            if uploaded.status_code != 200:
                raise MinerUError(f"MinerU object upload failed: HTTP {uploaded.status_code}")
            return batch_id

    async def get_batch_results(self, batch_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/extract-results/batch/{batch_id}", headers=self.headers)
            body = self._body(response)
            if body.get("code") != 0:
                raise MinerUError(f"MinerU query rejected: {body.get('msg', 'unknown error')}")
            records = (body.get("data") or {}).get("extract_result") or []
            return records if isinstance(records, list) else [records]

    async def wait_for_result(self, batch_id: str, filename: str) -> dict:
        while True:
            records = await self.get_batch_results(batch_id)
            result = next((r for r in records if r.get("file_name") == filename), records[0] if records else None)
            if result is None:
                raise MinerUError("MinerU batch contains no result for uploaded file")
            if result.get("state") == "done":
                return result
            if result.get("state") == "failed":
                raise MinerUError(result.get("err_msg") or "MinerU parsing failed")
            await asyncio.sleep(self.settings.mineru_poll_seconds)

    @staticmethod
    def _body(response: httpx.Response) -> dict:
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise MinerUError("MinerU returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise MinerUError("MinerU returned an unexpected response")
        return body
