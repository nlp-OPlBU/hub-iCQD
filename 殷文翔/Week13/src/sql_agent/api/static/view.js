/** 只通过文本 DOM 接口呈现外部内容，不解析模型返回的 HTML 或 Markdown。 */
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, action) {
  const node = element("button", "text-button", text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function listSection(title, items, className = "") {
  const section = element("section", `result-list ${className}`.trim());
  section.append(element("h3", "", title));
  const list = element("ul");
  for (const item of items) list.append(element("li", "", item));
  section.append(list);
  return section;
}

function codeDetails(title, value) {
  const details = element("details", "result-details");
  details.append(element("summary", "", title));
  const pre = element("pre");
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", title);
  pre.append(element("code", "", value));
  details.append(pre);
  return details;
}

function copyButton(label, text, feedback) {
  const node = button(label, async () => {
    node.disabled = true;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      feedback.textContent = "已复制";
      feedback.dataset.failed = "false";
    } catch {
      feedback.textContent = "复制失败，请手动选择文本复制。";
      feedback.dataset.failed = "true";
    } finally {
      node.disabled = false;
    }
  });
  return node;
}

function requestIdLine(requestId) {
  return element("p", "request-id", `请求 ID：${requestId}`);
}

export function createQuery(record, stop) {
  const user = element("article", "user-message");
  user.setAttribute("aria-label", "用户问题");
  user.append(element("span", "message-label", "你"), element("p", "user-text", record.question));
  const agent = element("article", "agent-message");
  agent.setAttribute("aria-label", "SQL Agent 回复");
  const header = element("div", "agent-header");
  header.append(element("span", "agent-dot"), element("span", "message-label", "SQL Agent"));
  const card = element("div", "response-card");
  const waiting = element("div", "waiting-line");
  waiting.setAttribute("role", "status");
  waiting.setAttribute("aria-live", "polite");
  const spinner = element("span", "spinner");
  spinner.setAttribute("aria-hidden", "true");
  waiting.append(spinner, element("span", "", "正在处理查询"));
  const elapsed = element("p", "wait-time", "已等待 0 秒");
  elapsed.setAttribute("aria-live", "off");
  const actions = element("div", "response-actions");
  actions.append(button("停止等待", stop));
  card.append(waiting, elapsed, actions);
  agent.append(header, card);
  return { user, agent, card, elapsed };
}

export function renderResult(card, output, requestId, edit) {
  const content = [element("p", "answer", output.answer)];
  if (output.evidence.length) content.push(listSection("证据", output.evidence));
  if (output.caveats.length) content.push(listSection("限制说明", output.caveats, "caveats"));
  const metadata = element("div", "metadata");
  if (output.tables.length) {
    const tables = element("span", "", "所选表 ");
    for (const table of output.tables) tables.append(element("span", "table-tag", table));
    metadata.append(tables);
  }
  if (output.sql !== null) {
    metadata.append(element("span", "", `保留结果 ${output.row_count} 行`), element("span", "", `SQL 修复 ${output.repair_attempts} 次`));
    if (output.row_count === 0) content.push(element("p", "notice", "查询返回 0 行。"));
    if (output.truncated) content.push(element("p", "notice", "执行结果已按行数上限截断，当前返回的是部分结果。"));
  } else {
    metadata.append(element("span", "", "本次未返回 SQL"));
  }
  content.push(metadata);
  if (output.sql !== null) content.push(codeDetails("查看 SQL", output.sql));
  content.push(codeDetails("查看响应 JSON", JSON.stringify(output, null, 2)));
  const feedback = element("span", "copy-feedback");
  feedback.setAttribute("role", "status");
  const actions = element("div", "response-actions");
  actions.append(copyButton("复制答案", output.answer, feedback));
  if (output.sql !== null) actions.append(copyButton("复制 SQL", output.sql, feedback));
  actions.append(button("编辑问题", edit), feedback);
  content.push(actions);
  if (output.sql !== null) content.push(element("p", "range-note", "答案可能基于部分结果，请结合证据、SQL 和限制说明判断。"));
  if (requestId) content.push(requestIdLine(requestId));
  card.replaceChildren(...content);
}

export function renderFailure(card, error, edit, check) {
  card.classList.add("error-card");
  const heading = element("h3", "error-title", "本次查询未能完成");
  heading.setAttribute("role", "alert");
  const content = [heading, element("p", "answer", error.message)];
  if (error.details?.length) {
    content.push(listSection("请求详情", error.details.map((item) => `${item.field === "question" ? "问题" : "请求体"}：${item.message}`)));
  }
  const actions = element("div", "response-actions");
  actions.append(button("编辑问题", edit), button("重新检查服务", check));
  content.push(actions);
  if (error.status) content.push(element("p", "range-note", `HTTP ${error.status} · ${error.code}`));
  if (error.requestId) content.push(requestIdLine(error.requestId));
  card.replaceChildren(...content);
}

export function renderStopped(card, edit) {
  const actions = element("div", "response-actions");
  actions.append(button("编辑问题", edit));
  card.replaceChildren(
    element("p", "answer", "已停止等待，服务端可能仍在处理。"),
    element("p", "range-note", "停止等待不会保证取消服务端查询；再次提交可能与之前的请求并行。"), actions,
  );
}
