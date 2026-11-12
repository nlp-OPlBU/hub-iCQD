import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressPanel } from "@/components/ProgressPanel";
import type { ResearchProcess } from "@/lib/types";

const process: ResearchProcess = {
  plan: ["Agent 框架", "对比评测"],
  search_queries: ["Agent 框架"],
  reviewed_urls: ["https://example.com/a"],
  iterations: 1,
  steps: [
    { type: "plan", round: 0, detail: { keywords: ["Agent 框架", "对比评测"] } },
    { type: "search", round: 1, detail: { keyword: "Agent 框架", results: 8 } },
    { type: "summarize", round: 1, detail: { keyword: "Agent 框架", chars: 320 } },
    { type: "judge", round: 1, detail: { sufficient: false, reason: "缺少竞品对比", new_keywords: ["LangGraph"] } },
  ],
};

describe("ProgressPanel", () => {
  it("渲染轮数与统计", () => {
    render(<ProgressPanel process={process} draftCount={1} sourcesCount={8} />);
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/1 段正文/)).toBeInTheDocument();
    expect(screen.getByText(/8 个来源/)).toBeInTheDocument();
  });

  it("渲染各类型 step 的时间线条目", () => {
    render(<ProgressPanel process={process} draftCount={1} sourcesCount={8} />);
    expect(screen.getByText(/Agent 框架、对比评测/)).toBeInTheDocument();
    expect(screen.getByText(/检索「Agent 框架」/)).toBeInTheDocument();
    expect(screen.getByText(/总结「Agent 框架」/)).toBeInTheDocument();
    expect(screen.getByText(/缺少竞品对比/)).toBeInTheDocument();
    expect(screen.getByText(/LangGraph/)).toBeInTheDocument();
  });

  it("空过程显示占位", () => {
    const empty: ResearchProcess = { plan: [], search_queries: [], reviewed_urls: [], iterations: 0, steps: [] };
    render(<ProgressPanel process={empty} draftCount={0} sourcesCount={0} />);
    expect(screen.getByText(/等待第一步/)).toBeInTheDocument();
  });

  it("judge 缺省 reason 时不渲染空括号", () => {
    const p: ResearchProcess = {
      ...process,
      steps: [{ type: "judge", round: 1, detail: { sufficient: true } }],
    };
    render(<ProgressPanel process={p} draftCount={1} sourcesCount={8} />);
    expect(screen.getByText(/判断：信息足够/)).toBeInTheDocument();
    expect(screen.queryByText(/（）/)).not.toBeInTheDocument();
  });
});
