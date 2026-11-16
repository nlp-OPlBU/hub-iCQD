import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { api } from "@/lib/api";
import type { ResearchRecord } from "@/lib/types";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({
  api: { health: vi.fn(), listResearch: vi.fn(), startResearch: vi.fn() },
}));

const rec: ResearchRecord = {
  research_id: "r1", topic: "主题一", status: "completed",
  created_at: "2026-09-07T10:00:00+00:00", updated_at: "2026-09-07T10:00:00+00:00",
  error: null, report: null, report_html: "", sources: [], draft: [], process: null, confidence: null,
};

describe("首页", () => {
  beforeEach(() => vi.clearAllMocks());

  it("渲染表单与历史列表", async () => {
    vi.mocked(api.health).mockResolvedValue({ status: "ok" });
    vi.mocked(api.listResearch).mockResolvedValue([rec]);
    render(<HomePage />);
    expect(await screen.findByText("主题一")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/研究主题/)).toBeInTheDocument();
  });

  it("后端未启动时显示提示条，点击重试再次探测并刷新列表", async () => {
    vi.mocked(api.health).mockRejectedValueOnce(new TypeError("fetch failed")).mockResolvedValue({ status: "ok" });
    vi.mocked(api.listResearch).mockResolvedValueOnce([]).mockResolvedValueOnce([rec]);
    const user = userEvent.setup();
    render(<HomePage />);
    expect(await screen.findByText(/后端未启动/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /重试/ }));
    expect(await screen.findByText(/后端已连接/)).toBeInTheDocument();
    expect(api.health).toHaveBeenCalledTimes(2);
    // 重试成功后历史列表也要刷新：第一次为空、第二次返回记录
    expect(await screen.findByText("主题一")).toBeInTheDocument();
    expect(api.listResearch).toHaveBeenCalledTimes(2);
  });

  it("表单提交成功后跳转详情页", async () => {
    vi.mocked(api.health).mockResolvedValue({ status: "ok" });
    vi.mocked(api.listResearch).mockResolvedValue([]);
    vi.mocked(api.startResearch).mockResolvedValue({ research_id: "rid9", status: "pending" });
    const user = userEvent.setup();
    render(<HomePage />);
    await user.type(screen.getByPlaceholderText(/研究主题/), "测试主题");
    await user.click(screen.getByRole("button", { name: /开始研究/ }));
    expect(push).toHaveBeenCalledWith("/research/rid9");
  });
});
