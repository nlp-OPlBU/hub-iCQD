import assert from "node:assert/strict";
import { test } from "node:test";
import { APIError, checkReadiness, prepareQuestion, query } from "../../src/sql_agent/api/static/api-client.js";

const output = {
  question: "查询", answer: "答案", sql: "SELECT 1", tables: [], row_count: 1,
  truncated: false, repair_attempts: 0, evidence: ["1"], caveats: [], error: null,
};
const response = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json; charset=utf-8", "X-Request-ID": "server-id" },
});

test("输入去空白并按 Unicode 码点计数", () => {
  assert.equal(prepareQuestion(" \n查询😀\t ").length, 3);
  assert.equal(prepareQuestion("😀".repeat(2000)).error, "");
  assert.ok(prepareQuestion("😀".repeat(2001)).error);
  assert.ok(prepareQuestion(" \n\t").error);
});

test("只发送 question，保留成功响应的未知字段及服务端请求 ID", async (t) => {
  const expected = { ...output, future_field: "kept" };
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/v1/queries");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(options.body), { question: "查询" });
    return response(expected);
  });
  assert.deepEqual(await query("查询"), { output: expected, requestId: "server-id" });
});

test("澄清、空结果、汇总降级均为正常结果", async (t) => {
  for (const value of [
    { ...output, sql: null, row_count: 0 },
    { ...output, row_count: 0 },
    { ...output, answer: "| Name |\n| --- |\n| A |", caveats: ["汇总降级"] },
  ]) {
    const mock = t.mock.method(globalThis, "fetch", async () => response(value));
    assert.deepEqual((await query("查询")).output, value);
    mock.mock.restore();
  }
});

test("HTTP 错误保留详情，不自动重试", async (t) => {
  for (const status of [415, 422, 500, 503, 404, 405]) {
    const error = { code: "test_error", message: "请求失败", details: [{ field: "question", message: "请补充问题" }] };
    const mock = t.mock.method(globalThis, "fetch", async () => response({ error }, status));
    await assert.rejects(query("查询"), (caught) => caught instanceof APIError
      && caught.status === status && caught.code === error.code && caught.requestId === "server-id"
      && caught.details[0].message === "请补充问题");
    assert.equal(mock.mock.callCount(), 1);
    mock.mock.restore();
  }
});

test("非 JSON、非法 JSON、错误结构和非空业务 error 不进入成功视图", async (t) => {
  const invalid = [
    () => new Response("<html>private error</html>", { headers: { "Content-Type": "text/html" } }),
    () => new Response("{", { headers: { "Content-Type": "application/json" } }),
    ...[null, [], {}, { ...output, error: "failed" }, { ...output, sql: undefined },
      { ...output, row_count: -1 }, { ...output, row_count: 1.5 }, { ...output, truncated: "false" },
      { ...output, evidence: [123] }, { ...output, tables: null }, { ...output, repair_attempts: -1 },
    ].map((value) => () => response(value)),
    () => response({ error: { code: "bad", message: "bad", details: ["invalid"] } }, 422),
  ];
  for (const createResponse of invalid) {
    const mock = t.mock.method(globalThis, "fetch", async () => createResponse());
    await assert.rejects(query("查询"), { code: "invalid_response" });
    mock.mock.restore();
  }
});

test("网络错误经过规范化，主动中断不被改成网络错误", async (t) => {
  const network = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("network private detail"); });
  await assert.rejects(query("查询"), (error) => error.code === "network_error" && !error.message.includes("private"));
  network.mock.restore();
  t.mock.method(globalThis, "fetch", async (_url, { signal }) => { signal.throwIfAborted(); });
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(query("查询", controller.signal), { name: "AbortError" });
});

test("就绪检查只接受 ready，不把存活状态当作就绪", async (t) => {
  const ready = t.mock.method(globalThis, "fetch", async () => response({ status: "ready" }));
  await checkReadiness();
  assert.equal(ready.mock.calls[0].arguments[0], "/readyz");
  ready.mock.restore();
  t.mock.method(globalThis, "fetch", async () => response({ status: "ok" }));
  await assert.rejects(checkReadiness(), { code: "invalid_response" });
});
