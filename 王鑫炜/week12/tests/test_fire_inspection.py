import asyncio
import io
import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from PIL import Image
from pydantic import ValidationError

from common import Settings
from fire_inspection.app import MAX_UPLOAD, create_app, normalize_image
from fire_inspection.schemas import Observation, classify
from fire_inspection.vision import QwenVision, parse_observation


def observation(**updates):
    values = dict(scene="corridor", image_quality="clear", electric_vehicle=False, battery=False,
                  flying_wire_charging=False, flame_or_smoke=False, clutter="none", combustibles="none",
                  evidence=["走廊地面可见，未见堆物"], uncertainties=[])
    return Observation(**{**values, **updates})


def image_bytes(size=(128, 96), fmt="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", size, "gray").save(output, format=fmt)
    return output.getvalue()


@pytest.mark.parametrize("changes,expected", [
    ({"electric_vehicle": True}, "high"), ({"battery": True}, "high"),
    ({"flying_wire_charging": True}, "high"), ({"flame_or_smoke": True}, "high"),
    ({"clutter": "major"}, "medium"), ({"combustibles": "large"}, "medium"),
    ({"clutter": "minor"}, "low"), ({"combustibles": "small"}, "low"),
    ({}, "none"), ({"scene": "non_corridor", "electric_vehicle": True}, "non_corridor"),
    ({"electric_vehicle": True, "clutter": "major"}, "high"),
    ({"clutter": "major", "combustibles": "uncertain"}, "medium"),
])
def test_levels_and_priority(changes, expected):
    assert classify(observation(**changes)).risk_level == expected


@pytest.mark.parametrize("changes", [{"scene": "uncertain"}, {"image_quality": "insufficient"},
                                     {"clutter": "uncertain"}, {"combustibles": "uncertain"}])
def test_insufficient_evidence_never_becomes_safe(changes):
    result = classify(observation(**changes))
    assert result.status == "needs_review" and result.risk_level is None


def test_visible_high_risk_not_downgraded_by_quality():
    assert classify(observation(battery=True, image_quality="insufficient")).risk_level == "high"


def test_model_contract_and_fenced_json():
    expected = observation()
    assert parse_observation("```json\n" + expected.model_dump_json() + "\n```") == expected
    for content in ['{"risk_level":"none"}', "不是 JSON", expected.model_dump_json().replace('false', '"false"')]:
        with pytest.raises((ValidationError, ValueError)):
            parse_observation(content)


def test_image_rotated_resized_and_metadata_removed():
    source = Image.new("RGB", (2000, 1000))
    exif = source.getexif()
    exif[274] = 6
    output = io.BytesIO()
    source.save(output, format="JPEG", exif=exif)
    normalized = normalize_image(output.getvalue())
    with Image.open(io.BytesIO(normalized)) as image:
        assert image.size == (800, 1600)
        assert not image.getexif()


class FixedVision:
    def __init__(self, value=None, error=None):
        self.value = value or observation()
        self.error = error
        self.calls = 0

    async def analyze(self, jpeg):
        self.calls += 1
        assert jpeg.startswith(b"\xff\xd8")
        if self.error:
            raise self.error
        return self.value


def test_upload_api_end_to_end_with_injected_vision():
    model = FixedVision(observation(electric_vehicle=True))
    with TestClient(create_app(analyzer=model)) as client:
        result = client.post("/api/inspect", files={"image": ("phone.png", image_bytes(fmt="PNG"), "image/png")})
        assert result.status_code == 200
        assert result.json()["risk_level"] == "high"
        assert result.json()["observations"]["electric_vehicle"] is True
        for path in ["/", "/static/app.js", "/static/styles.css", "/health", "/api/config"]:
            assert client.get(path).status_code == 200
    assert model.calls == 1


@pytest.mark.parametrize("raw,status", [(b"", 400), (b"not an image", 400),
                                         (b"a" * (MAX_UPLOAD + 1), 413), (image_bytes((10, 10)), 400),
                                         (image_bytes(fmt="GIF"), 400)])
def test_invalid_upload_does_not_call_model(raw, status):
    model = FixedVision()
    with TestClient(create_app(analyzer=model)) as client:
        result = client.post("/api/inspect", files={"image": ("spoof.jpg", raw, "image/jpeg")})
    assert result.status_code == status and model.calls == 0


def test_missing_key_is_explicit_503():
    with TestClient(create_app(settings=Settings())) as client:
        result = client.post("/api/inspect", files={"image": ("x.jpg", image_bytes(), "image/jpeg")})
        assert result.status_code == 503
        assert client.get("/api/config").json()["configured"] is False


def test_timeout_is_not_a_risk_assessment():
    with TestClient(create_app(analyzer=FixedVision(error=asyncio.TimeoutError()))) as client:
        result = client.post("/api/inspect", files={"image": ("x.jpg", image_bytes(), "image/jpeg")})
    assert result.status_code == 504 and "risk_level" not in result.json()


def test_invalid_model_output_is_502():
    with pytest.raises(ValidationError) as invalid:
        Observation.model_validate({})
    with TestClient(create_app(analyzer=FixedVision(error=invalid.value))) as client:
        result = client.post("/api/inspect", files={"image": ("x.jpg", image_bytes(), "image/jpeg")})
    assert result.status_code == 502 and "risk_level" not in result.json()


def test_real_sdk_multimodal_request_with_mock_transport(monkeypatch):
    import json
    from fire_inspection import vision
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert request.url.path == "/v1/chat/completions"
        assert data["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert data["enable_thinking"] is False
        return httpx.Response(200, json={"id": "vision-test", "object": "chat.completion", "created": 0,
            "model": "qwen3-vl-plus", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": observation().model_dump_json()}}]})

    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(vision, "AsyncOpenAI", factory)
    model = QwenVision(Settings(api_key="test-key", base_url="http://localhost/v1"))
    assert asyncio.run(model.analyze(image_bytes())).scene == "corridor"
    assert len(requests) == 1
