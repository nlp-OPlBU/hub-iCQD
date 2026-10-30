"""在安装后端依赖的环境中运行：python -m unittest test_backend -v。"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DEEPSEEK_API_KEY", "offline-test-key")
os.environ.setdefault("DeepSeek_URL", "https://example.invalid/v1")

from agents import AgentOutputSchema, ModelBehaviorError
from fastapi import HTTPException

from backend import process_image
from models import Result


class ModelOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_structured_output(self):
        expected = Result(classification="低风险", description="少量堆物。")
        with patch("backend.Runner.run", new_callable=AsyncMock) as run:
            run.return_value = SimpleNamespace(final_output=expected)
            self.assertEqual(await process_image([]), expected)

    async def test_invalid_output_returns_readable_error(self):
        with patch("backend.Runner.run", new_callable=AsyncMock) as run:
            run.side_effect = ModelBehaviorError("Invalid JSON when parsing model output")
            with self.assertRaises(HTTPException) as error:
                await process_image([])
            self.assertEqual(error.exception.status_code, 502)
            self.assertIn("classification", error.exception.detail)

    async def test_sdk_rejects_text_and_invalid_classifications(self):
        schema = AgentOutputSchema(Result)
        for text in (
            "该楼道存在低风险。",
            '```json\n{"classification":"低风险","description":"少量堆物"}\n```',
            '{"classification":"中等风险","description":"大量堆物"}',
        ):
            with self.subTest(text=text), self.assertRaises(ModelBehaviorError):
                schema.validate_json(text)


if __name__ == "__main__":
    unittest.main()
