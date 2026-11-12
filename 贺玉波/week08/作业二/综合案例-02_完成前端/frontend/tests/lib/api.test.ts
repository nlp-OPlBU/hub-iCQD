import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../mocks/server";
import { ApiError, api } from "@/lib/api";

const API = "http://127.0.0.1:8000";

describe("api", () => {
  it("health 返回状态", async () => {
    server.use(http.get(`${API}/health`, () => HttpResponse.json({ status: "ok" })));
    const r = await api.health();
    expect(r.status).toBe("ok");
  });

  it("startResearch POST 主题并返回 research_id", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/api/research`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ research_id: "abc123", status: "pending" }, { status: 202 });
      })
    );
    const r = await api.startResearch("  Agent 框架  ");
    expect(r.research_id).toBe("abc123");
    expect(body).toEqual({ topic: "  Agent 框架  " });
  });

  it("getResearch 按 id 获取记录", async () => {
    const rec = { research_id: "abc123", topic: "t", status: "running", created_at: "", updated_at: "", error: null, report: null, report_html: "", sources: [], draft: [], process: null, confidence: null };
    server.use(http.get(`${API}/api/research/abc123`, () => HttpResponse.json(rec)));
    const r = await api.getResearch("abc123");
    expect(r.research_id).toBe("abc123");
    expect(r.status).toBe("running");
  });

  it("listResearch 返回数组", async () => {
    server.use(http.get(`${API}/api/research`, () => HttpResponse.json([])));
    expect(await api.listResearch()).toEqual([]);
  });

  it("非 2xx 抛 ApiError 且携带后端 detail", async () => {
    server.use(
      http.post(`${API}/api/research`, () =>
        HttpResponse.json({ detail: "topic 不能为空" }, { status: 400 })
      )
    );
    await expect(api.startResearch("x")).rejects.toMatchObject({ status: 400, message: "topic 不能为空" });
    await expect(api.startResearch("x")).rejects.toBeInstanceOf(ApiError);
  });

  it("404 抛 ApiError(status=404)", async () => {
    server.use(
      http.get(`${API}/api/research/nope`, () =>
        HttpResponse.json({ detail: "research 不存在" }, { status: 404 })
      )
    );
    await expect(api.getResearch("nope")).rejects.toMatchObject({ status: 404 });
  });
});
