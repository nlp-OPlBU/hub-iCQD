import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResearchForm } from "@/components/ResearchForm";
import { ApiError, api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { startResearch: vi.fn() },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

describe("ResearchForm", () => {
  beforeEach(() => vi.clearAllMocks());

  it("空输入时提交按钮禁用", () => {
    render(<ResearchForm onCreated={vi.fn()} />);
    expect(screen.getByRole("button", { name: /开始研究/ })).toBeDisabled();
  });

  it("提交后调用 startResearch 并回调 onCreated", async () => {
    vi.mocked(api.startResearch).mockResolvedValue({ research_id: "rid1", status: "pending" });
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<ResearchForm onCreated={onCreated} />);
    await user.type(screen.getByPlaceholderText(/研究主题/), "  Agent 框架对比  ");
    await user.click(screen.getByRole("button", { name: /开始研究/ }));
    expect(api.startResearch).toHaveBeenCalledWith("Agent 框架对比");
    expect(onCreated).toHaveBeenCalledWith("rid1");
  });

  it("后端 400 时展示错误信息", async () => {
    vi.mocked(api.startResearch).mockRejectedValue(new ApiError(400, "topic 不能为空"));
    const user = userEvent.setup();
    render(<ResearchForm onCreated={vi.fn()} />);
    await user.type(screen.getByPlaceholderText(/研究主题/), "x");
    await user.click(screen.getByRole("button", { name: /开始研究/ }));
    expect(await screen.findByText("topic 不能为空")).toBeInTheDocument();
  });
});
