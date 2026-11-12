import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResearchList } from "@/components/ResearchList";
import type { ResearchRecord } from "@/lib/types";

const mkRec = (rid: string, topic: string, created_at: string, status: ResearchRecord["status"]): ResearchRecord => ({
  research_id: rid, topic, status, created_at, updated_at: created_at,
  error: null, report: null, report_html: "", sources: [], draft: [], process: null, confidence: null,
});

describe("ResearchList", () => {
  it("按 created_at 倒序渲染，点击回调 onSelect", async () => {
    const records = [
      mkRec("r1", "主题一", "2026-09-07T10:00:00+00:00", "completed"),
      mkRec("r2", "主题二", "2026-09-07T12:00:00+00:00", "running"),
    ];
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<ResearchList records={records} onSelect={onSelect} />);

    const rows = screen.getAllByRole("button");
    expect(rows[0]).toHaveTextContent("主题二");
    expect(rows[0]).toHaveTextContent("研究中");
    expect(rows[1]).toHaveTextContent("主题一");
    expect(rows[1]).toHaveTextContent("已完成");

    await user.click(rows[0]);
    expect(onSelect).toHaveBeenCalledWith("r2");
  });

  it("空列表显示空态", () => {
    render(<ResearchList records={[]} onSelect={vi.fn()} />);
    expect(screen.getByText(/暂无研究记录/)).toBeInTheDocument();
  });
});
