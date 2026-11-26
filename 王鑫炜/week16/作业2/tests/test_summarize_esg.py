from datetime import datetime
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_esg.py"
SPEC = importlib.util.spec_from_file_location("summarize_esg", SCRIPT)
esg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(esg)


def row(identifier, **changes):
    original = {
        "id": identifier,
        "title": f"公告 {identifier}",
        "date": "2026-11-25",
        "url": f"https://example.org/notice/{identifier}",
        "category": "ESG披露与评级",
        "publisher": "测试采购人",
        "text": "采购可持续发展报告编制服务。",
        "notice_type": "award",
        "project_id": f"测试平台:{identifier}",
        "lot": "包1",
        "amount_kind": "award_total",
        "amount_cny": "100000",
    }
    return {**original, **changes}


def sample(*rows, **changes):
    return {
        "case": "procurement",
        "start": "2024-11-27",
        "end": "2026-11-26",
        "as_of": "2026-11-26",
        "subject": "ESG服务",
        "records": list(rows),
        **changes,
    }


class EsgSummaryTests(unittest.TestCase):
    def test_dedup_counts_distinct_lots_and_excludes_budget(self):
        output = "\n".join(esg.render_awards(sample(
            row("A"),
            row("B", project_id="测试平台:A", date="2026-11-26", amount_cny="500000"),
            row("C", project_id="测试平台:A", lot="包2", amount_cny="1000000"),
            row("D", amount_kind="unit_price", amount_cny="7"),
            row("E", notice_type="tender", amount_kind="budget", amount_cny="5000000"),
            row("F", date="2024-11-26"),
        )))
        self.assertIn("纳入 3 条中标结果", output)
        self.assertIn("可比人民币成交总价 2 条", output)
        self.assertIn("合计 1,500,000.00 元，中位数 750,000.000 元", output)
        self.assertIn("| 50 万元 ≤ 金额 < 100 万元 | 1 |", output)
        self.assertIn("| 100 万元 ≤ 金额 < 300 万元 | 1 |", output)
        self.assertIn("A · 重复项目包，保留 B", output)
        self.assertIn("F · 来源日期超出统计窗口", output)

    def test_daily_checks_registration_and_restrictive_same_day_notice(self):
        common = {
            "notice_type": "tender",
            "status": "open",
            "deadline": "2026-11-30T09:00:00+08:00",
            "needs_registration": True,
        }
        output = "\n".join(esg.render_daily(sample(
            row("A", **common, registration_deadline="2026-11-26T09:30:00+08:00"),
            row("B", **common, registration_deadline="2026-11-26T11:00:00+08:00"),
            row("C", **common, registration_deadline="2026-11-26"),
            row("D", **common, registration_deadline="2026-11-27"),
            row("CANCEL", project_id="测试平台:D", notice_type="cancelled"),
        ), esg.date(2026, 11, 25), datetime.fromisoformat("2026-11-26T02:00:00+00:00")))
        self.assertIn("开放招标 1 条，待人工核验 1 条", output)
        self.assertIn("公告 B", output)
        self.assertIn("报名或文件获取已截止", output)
        self.assertIn("D · 存在优先的已核验公告，保留 CANCEL", output)

    def test_same_day_tender_respects_exact_publication_cutoff(self):
        common = {
            "date": "2026-11-26",
            "notice_type": "tender",
            "status": "open",
            "deadline": "2026-12-01T09:00:00+08:00",
        }
        cutoff = datetime.fromisoformat("2026-11-26T10:00:00+08:00")
        output = "\n".join(esg.render_daily(sample(
            row("before", **common, published_at="2026-11-26T09:59:59+08:00"),
            row("after", **common, published_at="2026-11-26T10:01:00+08:00"),
            row("uncertain", **common),
        ), None, cutoff))
        self.assertIn("开放招标 1 条，待人工核验 1 条", output)
        self.assertIn("公告 before", output)
        self.assertIn("after · 晚于日报判断截点", output)
        self.assertIn("公告 uncertain · 缺少当日具体发布时间", output)

    def test_monthly_preserves_date_basis_and_url_identity(self):
        rows = [
            row("A", date="2026-10-01", url="https://example.org/?id=17&utm_source=a",
                category="政策与标准"),
            row("B", date="2026-10-25", url="http://example.org/?id=17&utm_source=b",
                category="政策与标准", date_basis="意见截止，原文发布于9月"),
            row("C", date="2026-10-26", url="https://example.org/?id=18",
                category="政策与标准"),
            row("D", date="2026-09-30", category="政策与标准"),
        ]
        output = "\n".join(esg.render_monthly(sample(
            *rows, case="monthly", start="2026-10-01", end="2026-10-31"
        )))
        self.assertIn("纳入 2 条", output)
        self.assertIn("2026-10-25（意见截止，原文发布于9月）", output)
        self.assertIn("A · 重复来源，保留 B", output)
        self.assertIn("D · 事实日期超出月报窗口", output)

    def test_failed_search_snapshot_visible_and_untrusted_link_skipped(self):
        output = "\n".join(esg.render_candidates({
            "provider": "test",
            "status": "partial",
            "errors": [{"query": "招标", "message": "超时"}],
            "results": [
                {"title": "<img>", "url": "https://example.org/", "description": "摘要。"},
                {"title": "invalid", "url": "javascript:alert(1)"},
            ],
        }, 4))
        self.assertIn("请求状态 · partial", output)
        self.assertIn("招标 · 超时", output)
        self.assertIn("&lt;img&gt;", output)
        self.assertNotIn("javascript", output)


if __name__ == "__main__":
    unittest.main()
