from deep_research.cli import main


def test_cli_mock_end_to_end(tmp_path, capsys):
    rc = main(["--mock", "--output-dir", str(tmp_path), "测试主题"])
    assert rc == 0
    out_dir = tmp_path / "测试主题"
    for name in ("report.md", "sources.md", "process.md", "report.html", "output.json"):
        assert (out_dir / name).exists(), f"缺少输出文件 {name}"
    report_md = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "模型推断" in report_md
    assert "置信度说明" in report_md
    html = (out_dir / "report.html").read_text(encoding="utf-8")
    assert "<html" in html and "来源列表" in html
    captured = capsys.readouterr().out
    assert "[规划]" in captured and "[综合]" in captured


def test_cli_missing_key_suggests_mock(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    rc = main(["--output-dir", "outputs", "主题"])
    assert rc != 0
    assert "--mock" in capsys.readouterr().err


def test_cli_missing_bocha_key_hints_mock(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    rc = main(["--output-dir", "outputs", "主题"])
    assert rc != 0
    assert "--mock" in capsys.readouterr().err
