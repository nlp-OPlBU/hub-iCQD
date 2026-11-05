/** 可选浏览器验收：需要独立安装 playwright-core 和本机浏览器，不影响网页运行。 */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright-core");
const staticRoot = new URL("../../src/sql_agent/api/static/", import.meta.url);
const resources = new Set(["index.html", "styles.css", "main.js", "api-client.js", "view.js"]);
const server = createServer(async (request, response) => {
  const name = request.url === "/" ? "index.html" : request.url.replace(/^\/static\//, "");
  if (!resources.has(name)) { response.writeHead(404).end(); return; }
  response.writeHead(200, { "Content-Type": name.endsWith(".html") ? "text/html; charset=utf-8" : name.endsWith(".css") ? "text/css" : "text/javascript" });
  response.end(await readFile(new URL(name, staticRoot)));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
const output = (updates = {}) => ({
  question: "查询", answer: "数据库中共有 3503 首歌曲。", sql: "SELECT COUNT(*) AS TrackCount FROM tracks",
  tables: ["tracks"], row_count: 1, truncated: false, repair_attempts: 0,
  evidence: ["歌曲总数：3503"], caveats: [], error: null, ...updates,
});
const fulfill = (route, body, status = 200) => route.fulfill({
  status, contentType: "application/json", headers: { "X-Request-ID": "browser-test-request" }, body: JSON.stringify(body),
});
let checks = 0;
function passed(name) { checks += 1; console.log(`OK ${name}`); }

try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : { channel: "msedge" }),
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "zh-CN" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let calls = [];
  let handler = (route) => fulfill(route, output());
  await page.route("**/readyz", (route) => fulfill(route, { status: "ready" }));
  await page.route("**/api/v1/queries", async (route) => {
    calls.push(route.request().postDataJSON());
    await handler(route);
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const send = async (text) => {
    await page.locator("#question").fill(text);
    await page.locator("#send-query").click();
  };
  const clear = async () => {
    if (!await page.locator("#clear-history").isEnabled()) return;
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator("#clear-history").click();
  };
  await page.goto(base);
  await page.getByText("服务与数据库就绪", { exact: true }).waitFor();
  assert.equal(await page.locator(".example-card").count(), 3);
  await page.locator(".example-card").first().click();
  assert.equal(calls.length, 0);
  assert.equal(await page.locator("#question").inputValue(), "数据库中一共有多少首歌曲？");
  await page.locator("#send-query").click();
  await page.locator(".answer").filter({ hasText: "3503" }).waitFor();
  assert.deepEqual(calls[0], { question: "数据库中一共有多少首歌曲？" });
  assert.match(await page.locator(".metadata").innerText(), /保留结果 1 行/);
  await page.getByText("查看 SQL", { exact: true }).click();
  assert.equal(await page.locator("pre code").first().innerText(), output().sql);
  assert.match(await page.locator(".request-id").innerText(), /browser-test-request/);
  passed("正常查询、示例仅填入、SQL、证据和服务端请求 ID");

  await page.evaluate(() => {
    window.copiedValue = null;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text) => { window.copiedValue = text; } } });
  });
  await page.getByRole("button", { name: "复制 SQL", exact: true }).click();
  assert.equal(await page.evaluate(() => window.copiedValue), output().sql);
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error("denied"); }; });
  await page.getByRole("button", { name: "复制答案", exact: true }).click();
  await page.getByText("复制失败，请手动选择文本复制。", { exact: true }).waitFor();
  passed("复制成功与剪贴板权限失败");

  for (const [label, result, expected] of [
    ["澄清", output({ answer: "请补充查询范围。", sql: null, row_count: 0, tables: [] }), "本次未返回 SQL"],
    ["空结果", output({ answer: "未查询到符合条件的数据。", row_count: 0 }), "查询返回 0 行。"],
    ["降级与截断", output({ answer: "| Name |\n| --- |\n| A |", caveats: ["汇总降级"], truncated: true }), "汇总降级"],
  ]) {
    await clear();
    handler = (route) => fulfill(route, result);
    await send(label);
    await page.getByText(expected, { exact: true }).waitFor();
    assert.equal(await page.locator(".error-card").count(), 0);
    if (label === "澄清") assert.equal(await page.getByRole("button", { name: "复制 SQL", exact: true }).count(), 0);
    if (label === "降级与截断") assert.match(await page.locator(".notice").innerText(), /行数上限截断/);
  }
  passed("澄清、空结果、降级与截断");

  await clear();
  await page.locator("#question").fill("😀".repeat(2000));
  assert.equal(await page.locator("#character-count").innerText(), "2000 / 2000");
  assert.equal(await page.locator("#send-query").isEnabled(), true);
  await page.locator("#question").fill("😀".repeat(2001));
  assert.equal(await page.locator("#send-query").isDisabled(), true);
  await page.locator("#question").fill(" \n ");
  assert.equal(await page.locator("#send-query").isDisabled(), true);
  handler = (route) => fulfill(route, output());
  await page.locator("#question").fill("输入法问题");
  const previousCalls = calls.length;
  await page.locator("#question").dispatchEvent("compositionstart");
  await page.locator("#question").press("Enter");
  assert.equal(calls.length, previousCalls);
  await page.locator("#question").dispatchEvent("compositionend");
  await page.locator("#question").press("Shift+Enter");
  assert.match(await page.locator("#question").inputValue(), /\n/);
  await page.locator("#question").press("Enter");
  await page.locator(".answer").waitFor();
  assert.equal(calls.length, previousCalls + 1);
  passed("Unicode 长度、空白、中文输入法与换行快捷键");

  const errorCases = [
    (route) => fulfill(route, { error: { code: "validation_error", message: "参数不合法", details: [{ field: "question", message: "请补充问题" }] } }, 422),
    (route) => fulfill(route, { error: { code: "workflow_failed", message: "查询处理失败", details: [] } }, 500),
    (route) => fulfill(route, { error: { code: "service_unavailable", message: "服务未就绪", details: [] } }, 503),
    (route) => route.fulfill({ status: 502, contentType: "text/html", body: "<h1>private upstream error</h1>" }),
    (route) => fulfill(route, { ...output(), row_count: "1" }),
    (route) => route.abort("failed"),
  ];
  for (const fail of errorCases) {
    await clear();
    handler = fail;
    await send("失败测试");
    await page.locator(".error-card").waitFor();
    assert.equal(await page.locator(".error-card pre").count(), 0);
    assert.equal(await page.locator(".error-card h1").count(), 0);
    await page.getByRole("button", { name: "编辑问题", exact: true }).click();
    assert.equal(await page.locator("#question").inputValue(), "失败测试");
    assert.equal(await page.locator("#send-query").isEnabled(), true);
  }
  passed("HTTP 422/500/503、非 JSON、结构错误、断网及重新编辑");

  await clear();
  const malicious = '<img src=x onerror="window.injected=true"><script>window.injected=true</script>';
  handler = (route) => fulfill(route, output({ answer: malicious, evidence: [malicious], caveats: [malicious], sql: malicious, tables: [malicious] }));
  await send(malicious);
  await page.locator(".answer").waitFor();
  assert.equal(await page.locator(".answer").innerText(), malicious);
  assert.equal(await page.locator("#messages img, #messages script").count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.locator("#question").fill("保留草稿");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "编辑问题", exact: true }).click();
  assert.equal(await page.locator("#question").inputValue(), "保留草稿");
  passed("外部文本安全展示与替换草稿确认");

  await clear();
  // 拦截 fetch：模拟忽略 AbortSignal 的迟到响应，验证旧请求不能解除新请求的锁。
  await page.evaluate(() => {
    const nativeFetch = window.fetch.bind(window);
    window.pendingQueries = [];
    window.fetch = (url, options) => {
      if (url !== "/api/v1/queries") return nativeFetch(url, options);
      return new Promise((resolve) => window.pendingQueries.push({ resolve, options }));
    };
    window.finishQuery = (index, answer) => {
      window.pendingQueries[index].resolve(new Response(JSON.stringify({
        question: "查询", answer, sql: "SELECT 1", tables: [], row_count: 1,
        truncated: false, repair_attempts: 0, evidence: [], caveats: [], error: null,
      }), { headers: { "Content-Type": "application/json" } }));
    };
  });
  await send("旧问题");
  await page.locator("#question").fill("等待期间的下一条草稿");
  await page.locator("#question").press("Enter");
  assert.equal(await page.evaluate(() => window.pendingQueries.length), 1);
  await page.getByRole("button", { name: "停止等待", exact: true }).click();
  assert.equal(await page.locator("#question").inputValue(), "等待期间的下一条草稿");
  await page.locator("#send-query").click();
  await page.evaluate(() => window.finishQuery(0, "旧回复不应出现"));
  await page.waitForFunction(() => document.querySelector("#send-query").disabled);
  assert.equal(await page.locator("#messages").getByText("旧回复不应出现", { exact: true }).count(), 0);
  await page.evaluate(() => window.finishQuery(1, "新回复"));
  await page.locator(".answer").filter({ hasText: "新回复" }).waitFor();
  await send("清空前的问题");
  await clear();
  await send("清空后的问题");
  await page.evaluate(() => window.finishQuery(2, "清空前回复"));
  assert.equal(await page.locator("#messages").getByText("清空前回复", { exact: true }).count(), 0);
  assert.equal(await page.locator("#send-query").isDisabled(), true);
  await page.evaluate(() => window.finishQuery(3, "清空后回复"));
  await page.locator(".answer").filter({ hasText: "清空后回复" }).waitFor();
  passed("重复发送、停止等待、清空后的迟到响应和请求锁隔离");

  await clear();
  await page.setViewportSize({ width: 375, height: 812 });
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(await overflow(), false);
  await send("窄屏长内容");
  await page.evaluate(() => window.finishQuery(4, "长内容".repeat(200)));
  await page.locator(".answer").waitFor();
  await page.getByText("查看响应 JSON", { exact: true }).click();
  assert.equal(await overflow(), false);
  assert.equal(await page.locator("#question").isVisible(), true);
  if (process.env.SCREENSHOT_DIR) {
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/sql-agent-mobile.png`, fullPage: true });
    await clear();
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/sql-agent-desktop.png`, fullPage: true });
  }
  await page.reload();
  assert.equal(await page.locator("#messages article").count(), 0);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.deepEqual(errors, []);
  passed("375px 布局、长内容、刷新清空与无浏览器脚本异常");
  console.log(`${checks} browser checks passed`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
