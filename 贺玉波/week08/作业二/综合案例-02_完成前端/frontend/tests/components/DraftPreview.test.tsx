import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DraftPreview } from "@/components/DraftPreview";
import type { DraftBlock } from "@/lib/types";

describe("DraftPreview", () => {
  it("空草稿显示占位", () => {
    render(<DraftPreview draft={[]} />);
    expect(screen.getByText(/草稿尚未生成/)).toBeInTheDocument();
  });

  it("渲染各段草稿的轮次、关键词与正文", () => {
    const draft: DraftBlock[] = [
      { round: 1, keyword: "Agent 框架", text: "第一段正文。" },
      { round: 2, keyword: "LangGraph", text: "第二段正文。" },
    ];
    render(<DraftPreview draft={draft} />);
    expect(screen.getByText("第 1 轮 · Agent 框架")).toBeInTheDocument();
    expect(screen.getByText("第一段正文。")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮 · LangGraph")).toBeInTheDocument();
    expect(screen.getByText("第二段正文。")).toBeInTheDocument();
  });
});
