from pathlib import Path

from deep_research.agents import (
    PLANNER_SYSTEM, READER_SYSTEM, REFINER_SYSTEM, SYNTHESIZER_SYSTEM,
)

TPL = Path(__file__).parent.parent / "deep_research" / "templates"


def test_prompt_templates_exist_on_disk():
    """四个提示词模板必须存在于 deep_research/templates/，且各带角色关键词。"""
    for name, keyword in (
        ("planner_agent.jinja2", "规划"),
        ("reader_agent.jinja2", "抽取"),
        ("refiner_agent.jinja2", "补检"),
        ("synthesizer_agent.jinja2", "综合"),
    ):
        path = TPL / name
        assert path.exists(), f"缺少模板 {name}"
        assert keyword in path.read_text(encoding="utf-8"), f"模板 {name} 缺关键词「{keyword}」"


def test_agents_prompts_loaded_from_templates():
    """agents.py 导出的提示词常量来自模板文件而非硬编码。

    Jinja2 渲染默认去掉一个尾部换行（keep_trailing_newline=False），
    因此与磁盘文件比较时忽略尾部空白。"""
    assert PLANNER_SYSTEM == (TPL / "planner_agent.jinja2").read_text(encoding="utf-8").rstrip()
    assert READER_SYSTEM == (TPL / "reader_agent.jinja2").read_text(encoding="utf-8").rstrip()
    assert REFINER_SYSTEM == (TPL / "refiner_agent.jinja2").read_text(encoding="utf-8").rstrip()
    assert SYNTHESIZER_SYSTEM == (TPL / "synthesizer_agent.jinja2").read_text(encoding="utf-8").rstrip()
