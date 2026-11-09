"use strict";

const $ = (selector) => document.querySelector(selector);
const storageKey = "chinook-query-v1";
const suggestions = [
  ["销售概览", "所有发票的总金额是多少？", "trend"],
  ["客户规模", "一共有多少位客户？", "users"],
  ["客户排行", "消费金额最高的前 3 位客户是谁？", "rank"],
  ["条件筛选", "查询来自 Atlantis 的客户", "search"],
];
let config = null;
let tables = [];
let busy = false;
let conversations = [];
let activeId = null;

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}
function announce(text) { $("#announcement").textContent = text; }
function currentChat() { return conversations.find((chat) => chat.id === activeId); }
function persist() {
  try { sessionStorage.setItem(storageKey, JSON.stringify({ conversations, activeId })); }
  catch (_) { /* 存储被禁用或达到配额时，当前页面仍然可以正常对话。 */ }
}
function newChat() {
  if (busy) return;
  const chat = { id: crypto.randomUUID(), title: "新对话", messages: [] };
  conversations.unshift(chat);
  conversations = conversations.slice(0, 8);
  activeId = chat.id;
  $("#question").value = "";
  render();
  closeNavigation();
  persist();
  $("#question").focus();
}
function restore() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (!Array.isArray(saved?.conversations)) throw new Error("empty");
    conversations = saved.conversations.filter((chat) => typeof chat.id === "string" &&
      typeof chat.title === "string" && Array.isArray(chat.messages)).slice(0, 8);
    for (const chat of conversations) {
      chat.messages = chat.messages.filter((message) => ["user", "assistant"].includes(message.role) &&
        typeof message.content === "string" && !message.pending).slice(-30);
    }
    activeId = conversations.some((chat) => chat.id === saved.activeId) ? saved.activeId : conversations[0]?.id;
  } catch (_) { conversations = []; }
  if (!conversations.length) newChat();
  else render();
}
function renderHistory() {
  const target = $("#chat-history");
  target.replaceChildren();
  const populated = conversations.filter((chat) => chat.messages.length);
  if (!populated.length) target.append(element("p", "history-empty", "你的查询会出现在这里"));
  for (const chat of populated) {
    const button = element("button", `history-item${chat.id === activeId ? " active" : ""}`, chat.title);
    button.title = chat.title;
    button.disabled = busy;
    button.setAttribute("aria-current", chat.id === activeId ? "true" : "false");
    button.onclick = () => { activeId = chat.id; render(); closeNavigation(); persist(); };
    target.append(button);
  }
}
function setBusy(value) {
  busy = value;
  $("#send").disabled = busy || !config?.ready || !$("#question").value.trim();
  $("#new-chat").disabled = busy;
  $("#repair-demo").disabled = busy;
  $("#question").readOnly = busy;
  $("#chat-form").setAttribute("aria-busy", String(busy));
  for (const button of document.querySelectorAll(".example-card")) button.disabled = busy || !config?.ready;
  for (const button of document.querySelectorAll(".history-item,.retry-button")) button.disabled = busy;
}
function scrollToEnd() {
  const scroller = $("#conversation-scroll");
  scroller.scrollTop = scroller.scrollHeight;
}
function render() {
  const chat = currentChat();
  $("#chat-title").textContent = chat?.title || "新对话";
  $("#welcome").hidden = !!chat?.messages.length;
  $("#messages").replaceChildren();
  for (const message of chat?.messages || []) $("#messages").append(renderMessage(message));
  const last = [...(chat?.messages || [])].reverse().find((message) => message.result);
  renderSteps(last?.result?.trace?.map((node) => ({node, label: nodeLabels[node], failed: false})) || [], last?.result);
  renderHistory();
  setBusy(busy);
  if (chat?.messages.length) scrollToEnd();
  else $("#conversation-scroll").scrollTop = 0;
}
const nodeLabels = {
  parse_database: "读取数据库结构", resolve_question: "理解连续追问", select_tables: "选择相关数据表", generate_sql: "生成查询 SQL",
  validate_sql: "校验 SQL", execute_sql: "执行数据库查询", repair_sql: "修复 SQL",
  summarize: "整理查询回答", handle_failure: "查询结束",
};
function renderSteps(events, result) {
  const list = $("#run-steps");
  list.replaceChildren();
  $("#run-state").textContent = result ? (result.status === "success" ? "已完成" : "未完成") : events.length ? "进行中" : "尚未开始";
  if (!events.length) list.append(element("li", "run-placeholder", "发送问题后，在这里查看实际执行进度。"));
  for (const event of events) {
    const li = element("li", event.failed ? "step-error" : "");
    li.append(icon(event.failed ? "close" : "check"), element("span", "", event.label || event.node));
    list.append(li);
  }
}
function renderMessage(message) {
  if (message.role === "user") {
    const section = element("section", "message user-message");
    section.setAttribute("aria-label", "你的提问");
    section.append(element("div", "user-bubble", message.content));
    return section;
  }
  const section = element("section", "message assistant-message");
  section.setAttribute("aria-label", "查询助手回答");
  const header = element("div", "assistant-header");
  const avatar = element("span", "assistant-avatar"); avatar.append(icon("spark"));
  header.append(avatar, element("span", "", "Chinook 助手"));
  if (message.result) header.append(element("span", "answer-meta", `${message.result.status === "success" ? "查询完成" : "查询未完成"} · ${message.result.elapsed_ms} ms`));
  const content = element("div", "assistant-content");
  section.append(header, content);
  if (message.pending) {
    const status = element("div", "busy-status");
    status.append(element("span", "spinner"), element("span", "progress-label", "正在准备查询…"));
    content.append(status);
    return section;
  }
  if (message.result?.resolved_question && message.result.resolved_question !== message.result.question) {
    content.append(element("p", "query-context", "本次理解：" + message.result.resolved_question));
  }
  content.append(element("p", `answer-text${message.failed ? " error-text" : ""}`, message.content));
  if (message.result) {
    const result = message.result;
    if (result.results !== null) content.append(resultTable(result));
    if (result.sql) {
      const details = element("details", "sql-details");
      details.append(element("summary", "", "查看实际执行的 SQL"));
      const toolbar = element("div", "sql-toolbar");
      const button = element("button", "", "复制 SQL");
      button.prepend(icon("copy"));
      button.onclick = async () => {
        try { await navigator.clipboard.writeText(result.sql); announce("SQL 已复制"); button.lastChild.textContent = "已复制"; }
        catch (_) { announce("复制失败，可展开 SQL 后手动选择复制"); }
      };
      toolbar.append(element("span", "", "SQLITE · READ ONLY"), button);
      details.append(toolbar, element("pre", "", result.sql));
      content.append(details);
    }
    const badges = element("div", "query-badges");
    for (const name of result.selected_tables) badges.append(element("span", "", name));
    if (result.retry_count) badges.append(element("span", "", `已修复 ${result.retry_count} 次`));
    if (result.mode === "demo") badges.append(element("span", "", result.results !== null ? "离线示例 · 实际查询" : "离线示例"));
    content.append(badges);
    if (result.fails.length) {
      const errors = element("details", "sql-details");
      errors.append(element("summary", "", `查看诊断记录（${result.fails.length}）`), element("pre", "", result.fails.join("\n")));
      content.append(errors);
    }
  }
  if (message.failed) {
    const retry = element("button", "retry-button", "编辑问题后重试");
    retry.onclick = () => {
      if (busy) return;
      const messages = currentChat().messages;
      const index = messages.indexOf(message);
      $("#question").value = messages[index - 1]?.content || "";
      setBusy(false); $("#question").focus();
    };
    content.append(retry);
  }
  return section;
}
function resultTable(result) {
  const box = element("div", "result-box");
  const heading = element("div", "result-heading");
  const label = element("span", "", `查询结果 · ${result.results.length} 行${result.truncated ? "预览" : ""}`);
  label.prepend(icon("db"));
  const download = element("button", "icon-button");
  download.setAttribute("aria-label", "下载查询结果 CSV"); download.title = "下载 CSV";
  download.disabled = !result.results.length; download.append(icon("down"));
  download.onclick = () => downloadCSV(result.results);
  heading.append(label, download); box.append(heading);
  if (!result.results.length) { box.append(element("div", "empty-result", "查询已成功执行，没有符合条件的数据。")); return box; }
  const wrap = element("div", "table-wrap");
  wrap.setAttribute("tabindex", "0"); wrap.setAttribute("aria-label", "查询结果表格，可横向滚动");
  const table = element("table");
  const columns = Object.keys(result.results[0]);
  const thead = element("thead"); const tr = element("tr");
  const tbody = element("tbody");
  const draw = (rows) => {
    tbody.replaceChildren();
    for (const row of rows) {
      const line = element("tr");
      for (const col of columns) line.append(element("td", "", row[col] === null ? "NULL" : row[col]));
      tbody.append(line);
    }
  };
  for (const col of columns) {
    const th = element("th"); th.scope = "col"; th.setAttribute("aria-sort", "none");
    const button = element("button", "", `${col} ↕`); button.title = `按 ${col} 排序`;
    button.onclick = () => {
      const asc = th.getAttribute("aria-sort") !== "ascending";
      for (const sibling of tr.children) sibling.setAttribute("aria-sort", "none");
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      const rows = [...result.results].sort((a, b) => {
        const x = a[col], y = b[col];
        const order = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), "zh-CN");
        return asc ? order : -order;
      }); draw(rows);
    };
    th.append(button); tr.append(th);
  }
  thead.append(tr); table.append(thead, tbody); draw(result.results); wrap.append(table); box.append(wrap);
  if (result.truncated) box.append(element("div", "truncation-note", "结果超过展示上限，当前仅为预览，不能据此计算全量总额或总数。"));
  return box;
}
function downloadCSV(rows) {
  const keys = Object.keys(rows[0]);
  const escape = (value) => {
    let text = value == null ? "" : String(value);
    if (typeof value === "string" && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const csv = [keys, ...rows.map((row) => keys.map((key) => row[key]))].map((row) => row.map(escape).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" }));
  const link = element("a"); link.href = url; link.download = "chinook-query.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000); announce("已下载查询结果 CSV");
}
function contextMessage(message) {
  let content = message.content;
  if (message.role === "assistant" && message.result?.status === "success") {
    const preview = JSON.stringify((message.result.results || []).slice(0, 3));
    content = "上次回答：" + message.content.slice(0, 600) +
      "\n上次实际 SQL：\n" + message.result.sql.slice(0, 1400) +
      "\n上次结果预览（可能截断）：\n" + preview.slice(0, 300);
  }
  return {role: message.role, content: content.slice(0, 2400)};
}
async function sendQuestion(question) {
  question = question.trim();
  if (!question || busy || !config?.ready) return;
  if (question.length > 2000) { announce("问题不能超过 2000 字"); return; }
  const chat = currentChat();
  const history = chat.messages.filter((message) => !message.pending && message.content).slice(-12)
    .map(contextMessage);
  if (!chat.messages.length) chat.title = question;
  chat.messages.push({role: "user", content: question});
  const answer = {role: "assistant", content: "", pending: true, events: []};
  chat.messages.push(answer);
  $("#question").value = "";
  setBusy(true); render(); announce("查询已开始");
  let terminal = false;
  const receive = (event) => {
    if (event.type === "node") {
      answer.events.push(event); renderSteps(answer.events);
      const label = $(".progress-label"); if (label) label.textContent = event.label + (event.failed ? "：准备处理错误…" : "，正在继续…");
    } else if (event.type === "done") {
      terminal = true; answer.pending = false; answer.result = event.result;
      answer.content = event.result.summary; answer.failed = event.result.status !== "success";
    } else if (event.type === "error") { throw new Error(event.message); }
  };
  try {
    const response = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question, history, inject_bad_sql: $("#repair-demo").checked})});
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "问题格式不正确或服务暂时不可用，请检查后重试。");
    }
    if (!response.body) throw new Error("当前浏览器不支持流式响应，请更新浏览器。");
    const reader = response.body.getReader();
    const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let index;
      while ((index = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1);
        if (line) receive(JSON.parse(line));
      }
      if (done) break;
    }
    if (buffer.trim()) receive(JSON.parse(buffer));
    if (!terminal) throw new Error("连接已中断，没有收到完整结果，请重试。");
    announce(answer.failed ? "本次查询未完成，请查看错误说明" : "查询完成，已显示回答及实际结果");
  } catch (error) {
    answer.pending = false; answer.failed = true;
    answer.content = error instanceof TypeError ? "无法连接查询服务，请确认后端已经启动后重试。" : error.message;
    announce(answer.content);
  } finally {
    setBusy(false); render(); renderSteps(answer.events, answer.result); persist();
    $("#question").focus(); scrollToEnd();
  }
}
function renderSchema() {
  const preferred = ["invoices", "customers", "tracks", "albums", "artists"];
  $("#table-list").replaceChildren();
  for (const name of preferred) {
    const table = tables.find((item) => item.name === name); if (!table) continue;
    const row = element("div", "table-row");
    row.append(icon("db"), element("span", "", table.name), element("small", "", table.row_count.toLocaleString()));
    $("#table-list").append(row);
  }
  const content = $("#schema-content"); content.replaceChildren();
  for (const table of tables) {
    const details = element("details", "schema-table"); const summary = element("summary", "", `${table.name} · ${table.label}`);
    summary.append(element("small", "", `${table.row_count.toLocaleString()} 行`));
    const columns = element("div", "schema-columns");
    for (const col of table.columns) {
      const line = element("div", "schema-column");
      line.append(element("span", "", col.name + (col.pk ? " · 主键" : "")), element("small", "", col.type)); columns.append(line);
    }
    details.append(summary, columns); content.append(details);
  }
}
function closeNavigation() {
  const wasOpen = $("#sidebar").classList.contains("open");
  $("#sidebar").classList.remove("open"); $("#scrim").hidden = true;
  $("#menu-toggle").setAttribute("aria-expanded", "false");
  if (wasOpen) $("#menu-toggle").focus();
}
$("#new-chat").onclick = newChat;
$("#chat-form").onsubmit = (event) => { event.preventDefault(); sendQuestion($("#question").value); };
$("#question").addEventListener("input", () => setBusy(busy));
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); if (!$("#send").disabled) $("#chat-form").requestSubmit();
  }
});
$("#menu-toggle").onclick = () => {
  const open = $("#sidebar").classList.toggle("open"); $("#scrim").hidden = !open;
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("#new-chat").focus();
};
$("#scrim").onclick = closeNavigation;
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeNavigation(); });
for (const button of document.querySelectorAll(".schema-trigger")) button.onclick = () => $("#schema-dialog").showModal();
$("#close-schema").onclick = () => $("#schema-dialog").close();
for (const [title, question, glyph] of suggestions) {
  const button = element("button", "example-card");
  const image = element("span", "example-icon"); image.append(icon(glyph));
  const text = element("span"); text.append(element("strong", "", title), element("small", "", question));
  button.append(image, text, element("span", "card-arrow", "↗"));
  button.onclick = () => sendQuestion(question); $("#examples").append(button);
}
restore();
(async function init() {
  try {
    const [configResponse, schemaResponse] = await Promise.all([fetch("/api/config"), fetch("/api/schema")]);
    if (!configResponse.ok || !schemaResponse.ok) throw new Error("无法读取数据库信息");
    config = await configResponse.json(); tables = (await schemaResponse.json()).tables;
    const providerLabel = config.provider_name || "真实模型";
    $("#mode-badge").textContent = config.mode === "demo" ? "离线演示" :
      `${providerLabel} · ${config.ready ? config.model : "待配置"}`;
    $("#mode-badge").title = config.mode === "demo" ? "离线固定示例，不调用大模型" :
      `${providerLabel} / ${config.model}${config.ready ? "（已填写配置，鉴权以实际请求为准）" : "（尚未填写 API Key）"}`;
    $("#connection").lastElementChild.textContent = config.ready ? "本地服务已连接" : "模型待配置";
    $("#repair-option").hidden = config.mode !== "demo";
    $("#mode-note").textContent = !config.ready ? config.setup_hint : config.mode === "demo" ?
      "离线演示支持上方四个示例问题；连接真实模型后可自由提问与连续追问。" :
      "可结合之前的查询继续追问。对话仅保留在当前浏览器标签页。";
    for (const item of document.querySelectorAll(".table-number")) item.textContent = tables.length;
    renderSchema(); setBusy(false);
  } catch (_) {
    $("#connection").lastElementChild.textContent = "连接失败";
    $("#mode-badge").textContent = "服务不可用";
    $("#mode-note").textContent = "无法连接后端，请启动 python web_app.py 后刷新页面。";
    announce("无法连接查询服务");
  }
})();
