import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceView } from "@/components/ConfidenceView";
import { ReportHtmlView } from "@/components/ReportHtmlView";
import { SourcesView } from "@/components/SourcesView";
import type { ConfidenceNote, Source } from "@/lib/types";

describe("ReportHtmlView", () => {
  it("iframe srcDoc 嵌入完整 HTML", () => {
    render(<ReportHtmlView html="<html><body>报告</body></html>" />);
    const iframe = screen.getByTitle("HTML 报告");
    expect(iframe).toHaveAttribute("srcDoc", "<html><body>报告</body></html>");
  });
});

describe("SourcesView", () => {
  const sources: Source[] = [
    { url: "https://example.com/a", title: "来源A", site_name: "站点A", snippet: "摘录", accessed_at: "2026-09-07" },
  ];

  it("渲染来源链接与元信息", () => {
    render(<SourcesView sources={sources} />);
    const link = screen.getByRole("link", { name: "来源A" });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(screen.getByText("站点A")).toBeInTheDocument();
    expect(screen.getByText(/2026-09-07/)).toBeInTheDocument();
  });

  it("空来源显示空态", () => {
    render(<SourcesView sources={[]} />);
    expect(screen.getByText(/暂无来源/)).toBeInTheDocument();
  });
});

describe("ConfidenceView", () => {
  const confidence: ConfidenceNote = {
    overall: "high",
    info_cutoff: "2026-09-06",
    notes: ["正文由 3 段组成。"],
  };

  it("渲染置信度等级、截止时间与说明", () => {
    render(<ConfidenceView confidence={confidence} />);
    expect(screen.getByText("高")).toBeInTheDocument();
    expect(screen.getByText(/2026-09-06/)).toBeInTheDocument();
    expect(screen.getByText("正文由 3 段组成。")).toBeInTheDocument();
  });

  it("medium/low 映射正确", () => {
    render(<ConfidenceView confidence={{ ...confidence, overall: "low" }} />);
    expect(screen.getByText("低")).toBeInTheDocument();
  });
});
