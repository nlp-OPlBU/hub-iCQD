import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResearchDetail } from "@/components/ResearchDetail";
import { useResearch } from "@/hooks/useResearch";
import type { ResearchRecord } from "@/lib/types";

vi.mock("@/hooks/useResearch");

const mkRec = (status: ResearchRecord["status"]): ResearchRecord => ({
  research_id: "r1", topic: "测试主题", status,
  created_at: "", updated_at: "", error: null,
  report: {
    title: "报告标题", summary: "摘要文字", sections: [],
    key_conclusions: [], open_questions: [],
  },
  report_html: "<html><body>完整报告</body></html>",
  sources: [],
  draft: [],
  process: { plan: [], search_queries: [], reviewed_urls: [], iterations: 1, steps: [{ type: "plan", round: 0, detail: { keywords: ["a"] } }] },
  confidence: { overall: "medium", info_cutoff: "2026-09-06", notes: [] },
});

const idle = { record: null, loading: true, error: null, notFound: false };

describe("ResearchDetail", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.clearAllMocks());

  it("running 时展示进度面板与提示", () => {
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, record: mkRec("running") });
    render(<ResearchDetail id="r1" />);
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/关闭页面不影响研究/)).toBeInTheDocument();
  });

  it("completed 时左侧展示报告并可切换 HTML，侧栏常驻来源与置信度", async () => {
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, record: mkRec("completed") });
    const user = userEvent.setup();
    render(<ResearchDetail id="r1" />);
    expect(screen.getByRole("heading", { name: "报告标题" })).toBeInTheDocument();
    // 来源与置信度常驻侧栏，无需点击页签
    expect(screen.getByText(/暂无来源/)).toBeInTheDocument();
    expect(screen.getByText("中")).toBeInTheDocument();
    // 仅报告 / HTML 报告两个页签
    expect(screen.queryByRole("tab", { name: "来源" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "置信度" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "HTML 报告" }));
    expect(screen.getByTitle("HTML 报告")).toBeInTheDocument();
  });

  it("running 时左侧实时展示草稿段落", () => {
    const rec = mkRec("running");
    rec.draft = [{ round: 1, keyword: "Agent 框架", text: "检索到的第一段正文。" }];
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, record: rec });
    render(<ResearchDetail id="r1" />);
    expect(screen.getByText("第 1 轮 · Agent 框架")).toBeInTheDocument();
    expect(screen.getByText("检索到的第一段正文。")).toBeInTheDocument();
  });

  it("failed 时展示错误与保留的中间结果", () => {
    const rec = mkRec("failed");
    rec.error = "Bocha 搜索超时";
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, record: rec });
    render(<ResearchDetail id="r1" />);
    expect(screen.getByText(/Bocha 搜索超时/)).toBeInTheDocument();
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
  });

  it("404 显示研究不存在", () => {
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, notFound: true });
    render(<ResearchDetail id="nope" />);
    expect(screen.getByText(/研究不存在/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回首页/ })).toHaveAttribute("href", "/");
  });

  it("无 record 时仍展示轮询失败提示条", () => {
    vi.mocked(useResearch).mockReturnValue({ record: null, loading: false, error: "连续多次获取研究状态失败，请检查后端是否运行", notFound: false });
    render(<ResearchDetail id="r1" />);
    expect(screen.getByText(/连续多次获取研究状态失败/)).toBeInTheDocument();
  });

  it("report_html 为空时隐藏 HTML 报告页签", () => {
    const rec = mkRec("completed");
    rec.report_html = "";
    vi.mocked(useResearch).mockReturnValue({ ...idle, loading: false, record: rec });
    render(<ResearchDetail id="r1" />);
    expect(screen.queryByRole("tab", { name: "HTML 报告" })).not.toBeInTheDocument();
  });
});
