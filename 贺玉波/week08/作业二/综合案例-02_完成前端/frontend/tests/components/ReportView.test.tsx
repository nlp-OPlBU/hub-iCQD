import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportView } from "@/components/ReportView";
import type { ReportContent } from "@/lib/types";

const report: ReportContent = {
  title: "2026 年主流 Agent 框架对比",
  summary: "对 OpenAI Agents SDK 与 LangGraph 的对比分析。",
  sections: [
    {
      heading: "Agent 框架",
      body: "第一段。\n第二段。",
      conclusions: [{ text: "模型推断的结论", sources: [], is_model_inference: true }],
    },
  ],
  key_conclusions: [
    { text: "各有侧重", sources: [{ url: "https://example.com/x", title: "示例来源" }], is_model_inference: false },
  ],
  open_questions: ["未来趋势如何？"],
};

describe("ReportView", () => {
  it("渲染标题、摘要与分节（body 按换行分段）", () => {
    render(<ReportView report={report} />);
    expect(screen.getByRole("heading", { level: 1, name: "2026 年主流 Agent 框架对比" })).toBeInTheDocument();
    expect(screen.getByText(/对比分析/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agent 框架" })).toBeInTheDocument();
    expect(screen.getByText("第一段。")).toBeInTheDocument();
    expect(screen.getByText("第二段。")).toBeInTheDocument();
  });

  it("模型推断结论带标注", () => {
    render(<ReportView report={report} />);
    expect(screen.getByText("模型推断的结论")).toBeInTheDocument();
    expect(screen.getByText("模型推断")).toBeInTheDocument();
  });

  it("关键结论带来源链接", () => {
    render(<ReportView report={report} />);
    const link = screen.getByRole("link", { name: "示例来源" });
    expect(link).toHaveAttribute("href", "https://example.com/x");
  });

  it("渲染遗留问题", () => {
    render(<ReportView report={report} />);
    expect(screen.getByText("未来趋势如何？")).toBeInTheDocument();
  });

  it("结论来源为非 http(s) 协议时不渲染链接，仅显示文本", () => {
    const unsafe: ReportContent = {
      ...report,
      key_conclusions: [
        { text: "带不安全来源的结论", sources: [{ url: "javascript:alert(1)", title: "点击我" }], is_model_inference: false },
      ],
    };
    render(<ReportView report={unsafe} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("点击我")).toBeInTheDocument();
  });
});
