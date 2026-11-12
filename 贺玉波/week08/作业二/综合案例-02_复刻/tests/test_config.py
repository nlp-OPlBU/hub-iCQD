from pathlib import Path
from deep_research.config import Config, load_config


def test_load_config_defaults(monkeypatch):
    for var in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_REASONING_EFFORT",
                "BOCHA_API_KEY", "BOCHA_BASE_URL", "DEEP_RESEARCH_MAX_ROUNDS",
                "DEEP_RESEARCH_MAX_TOTAL_QUERIES", "DEEP_RESEARCH_OUTPUT_DIR"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.deepseek_api_key is None
    assert cfg.deepseek_model == "deepseek-v4-pro"
    assert cfg.deepseek_reasoning_effort == "max"
    assert cfg.bocha_api_key is None
    assert cfg.max_rounds == 2
    assert cfg.max_total_queries == 30
    assert cfg.output_dir == Path("outputs")


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-custom")
    monkeypatch.setenv("DEEP_RESEARCH_MAX_ROUNDS", "5")
    cfg = load_config()
    assert cfg.deepseek_model == "deepseek-custom"
    assert cfg.max_rounds == 5
