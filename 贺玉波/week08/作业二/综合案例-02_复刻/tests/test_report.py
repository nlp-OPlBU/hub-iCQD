from deep_research.models import (
    Citation, Conclusion, Confidence, Report, ReportSection, UnsourcedClaim,
)
from deep_research.pipeline import ResearchOutput
from deep_research.report import (
    render_html, render_process_md, render_report_md, render_sources_md,
    slugify, write_outputs,
)


def make_report() -> Report:
    return Report(
        title="测试报告",
        summary="摘要内容。",
        sections=[ReportSection(heading="第一节", body="正文[1]。", citation_ids=["1"])],
        conclusions=[Conclusion(text="结论一", citation_ids=["1"], confidence="高")],
        open_questions=["遗留问题"],
        confidence=Confidence(
            overall="中", info_cutoff_time="2026-09-10",
            unsourced_claims=[UnsourcedClaim(text="无来源推断内容")]),
        citations=[Citation(id="1", title="来源一", url="https://a.com/1")],
    )


def test_render_report_md_contains_required_sections():
    md = render_report_md(make_report())
    for keyword in ("# 测试报告", "## 摘要", "## 第一节", "## 关键结论",
                    "## 遗留问题", "## 置信度说明", "信息截止时间：2026-09-10",
                    "模型推断", "[1]"):
        assert keyword in md


def test_render_sources_md_lists_citations():
    md = render_sources_md(make_report())
    assert "1. [来源一](https://a.com/1)" in md


def test_render_process_md_records_queries():
    from deep_research.models import GapDecision, RoundRecord
    md = render_process_md(
        [RoundRecord(round_no=1, sub_question_id="sq1", queries=["关键词"],
                     pages_read=10, new_findings=3,
                     gap_decision=GapDecision(need_more=False, reason="够了"))],
        "主题")
    assert "关键词" in md and "10" in md and "第 1 轮" in md


def test_render_html_is_self_contained():
    html = render_html(make_report(), [])
    assert "<html" in html and "<style" in html
    assert "测试报告" in html and "https://a.com/1" in html


def test_render_html_report_structure():
    """报告页结构：页眉 meta、引用锚点↔id 对应、「模型推断」印章、来源与过程区块。"""
    html = render_html(make_report(), [])
    assert 'id="src-1"' in html            # 来源锚点
    assert 'href="#src-1"' in html         # 引用角标指向锚点
    assert "信息截止时间" in html          # 页眉 meta
    assert "模型推断" in html              # 印章标签
    assert "来源列表" in html
    assert "研究过程记录" in html


def test_write_outputs_creates_five_files(tmp_path):
    out = ResearchOutput(report=make_report(), process=[], total_queries=5)
    files = write_outputs(out, "测试 主题!", out_dir=tmp_path)
    assert set(files.keys()) == {"report", "sources", "process", "html", "json"}
    for p in files.values():
        assert p.exists() and p.stat().st_size > 0
    assert (tmp_path / "测试_主题" / "report.md").exists()


def test_output_json_roundtrip(tmp_path):
    """output.json 完整保存结构化数据，可反序列化回模型（支持未来换模板重渲染）。"""
    import json

    from deep_research.models import RoundRecord, GapDecision
    out = ResearchOutput(
        report=make_report(),
        process=[RoundRecord(round_no=1, sub_question_id="sq1", queries=["关键词"],
                             pages_read=10, new_findings=3,
                             gap_decision=GapDecision(need_more=False, reason="够了"))],
        total_queries=5,
    )
    files = write_outputs(out, "测试 主题!", out_dir=tmp_path)
    data = json.loads(files["json"].read_text(encoding="utf-8"))
    assert data["topic"] == "测试 主题!"
    assert data["total_queries"] == 5
    report = Report.model_validate(data["report"])
    assert report.title == "测试报告"
    assert report.citations[0].url == "https://a.com/1"
    process = [RoundRecord.model_validate(p) for p in data["process"]]
    assert process[0].queries == ["关键词"]


def test_slugify():
    assert slugify("天空为什么是蓝色的？") == "天空为什么是蓝色的"
    assert slugify('a/b\\c:d*e?') == "a_b_c_d_e"
    assert slugify("   ") == "research"


def test_slugify_windows_edge_cases():
    assert slugify("CON") == "research"
    assert slugify("com3") == "research"
    assert slugify("..") == "research"
    assert slugify("普通主题") == "普通主题"
