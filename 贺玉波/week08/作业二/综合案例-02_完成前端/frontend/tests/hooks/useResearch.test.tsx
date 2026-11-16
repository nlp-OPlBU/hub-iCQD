import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearch } from "@/hooks/useResearch";
import { ApiError } from "@/lib/api";
import type { ResearchRecord } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: { getResearch: vi.fn() },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));

import { api } from "@/lib/api";

const mkRec = (status: ResearchRecord["status"]): ResearchRecord => ({
  research_id: "abc123", topic: "t", status,
  created_at: "", updated_at: "", error: null, report: null,
  report_html: "", sources: [], draft: [], process: null, confidence: null,
});

describe("useResearch", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("初始即拉取一次", async () => {
    vi.mocked(api.getResearch).mockResolvedValue(mkRec("running"));
    const { result } = renderHook(() => useResearch("abc123"));
    expect(result.current.loading).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.record?.status).toBe("running");
  });

  it("running 状态每 2 秒轮询", async () => {
    vi.mocked(api.getResearch).mockResolvedValue(mkRec("running"));
    renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(api.getResearch).toHaveBeenCalledTimes(3);
  });

  it("completed 后停止轮询", async () => {
    vi.mocked(api.getResearch)
      .mockResolvedValueOnce(mkRec("running"))
      .mockResolvedValue(mkRec("completed"));
    renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getResearch).toHaveBeenCalledTimes(2);
  });

  it("failed 后停止轮询", async () => {
    vi.mocked(api.getResearch)
      .mockResolvedValueOnce(mkRec("running"))
      .mockResolvedValue(mkRec("failed"));
    renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getResearch).toHaveBeenCalledTimes(2);
  });

  it("404 置 notFound 并停止轮询", async () => {
    vi.mocked(api.getResearch).mockRejectedValue(new ApiError(404, "research 不存在"));
    const { result } = renderHook(() => useResearch("nope"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.notFound).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getResearch).toHaveBeenCalledTimes(1);
  });

  it("网络失败 <3 次不报错，第 3 次报错", async () => {
    vi.mocked(api.getResearch).mockRejectedValue(new TypeError("fetch failed"));
    const { result } = renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.error).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.error).toContain("连续");
  });

  it("连续 3 次失败后 loading 置 false", async () => {
    vi.mocked(api.getResearch).mockRejectedValue(new TypeError("fetch failed"));
    const { result } = renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.error).not.toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("卸载后不再轮询", async () => {
    vi.mocked(api.getResearch).mockResolvedValue(mkRec("running"));
    const { unmount } = renderHook(() => useResearch("abc123"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const calls = vi.mocked(api.getResearch).mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getResearch).toHaveBeenCalledTimes(calls);
  });

  it("rid 切换时立即清空旧 record，加载后显示新 record", async () => {
    vi.mocked(api.getResearch)
      .mockResolvedValueOnce({ ...mkRec("running"), research_id: "a", topic: "研究A" })
      .mockResolvedValueOnce({ ...mkRec("running"), research_id: "b", topic: "研究B" });
    const { result, rerender } = renderHook(({ rid }) => useResearch(rid), {
      initialProps: { rid: "a" },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.record?.topic).toBe("研究A");
    rerender({ rid: "b" });
    // tick 前：旧 record 必须已清空，避免研究 B 的 URL 下短暂显示研究 A 的内容
    expect(result.current.record).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.record?.topic).toBe("研究B");
  });

  it("rid 切换后连续失败计数重置", async () => {
    vi.mocked(api.getResearch).mockRejectedValue(new TypeError("fetch failed"));
    const { result, rerender } = renderHook(({ rid }) => useResearch(rid), {
      initialProps: { rid: "a" },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.error).toBeNull();
    rerender({ rid: "b" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.error).toBeNull();
  });
});
