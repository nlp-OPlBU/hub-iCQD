import { APIError, checkReadiness, prepareQuestion, query } from "./api-client.js";
import { createQuery, renderFailure, renderResult, renderStopped } from "./view.js";

const ui = Object.fromEntries([
  "query-form", "question", "send-query", "character-count", "input-error", "clear-history",
  "conversation", "messages", "welcome", "record-count", "service-status", "check-service",
  "new-reply", "announcement",
].map((id) => [id, document.getElementById(id)]));
const workspace = document.querySelector(".workspace");

const state = { records: [], active: null, version: 0, nextId: 0, composing: false, probe: null };

function announce(text) {
  ui.announcement.textContent = text;
}

function inputError(message) {
  ui["input-error"].textContent = message;
  ui["input-error"].hidden = !message;
  ui.question.setAttribute("aria-invalid", String(Boolean(message)));
}

function updateComposer() {
  const { length, error } = prepareQuestion(ui.question.value);
  ui["character-count"].textContent = `${length} / 2000`;
  ui["character-count"].classList.toggle("over-limit", length > 2000);
  ui["send-query"].disabled = Boolean(state.active) || state.composing || Boolean(error);
  ui["send-query"].firstChild.textContent = state.active ? "等待中 " : "发送 ";
  ui["clear-history"].disabled = state.records.length === 0 && ui.question.value.length === 0;
  inputError(length > 2000 ? error : "");
}

function nearBottom() {
  const node = ui.conversation;
  return node.scrollHeight - node.scrollTop - node.clientHeight < 90;
}

function scrollToBottom() {
  ui.conversation.scrollTop = ui.conversation.scrollHeight;
  ui["new-reply"].hidden = true;
}

function afterReply(wasNearBottom) {
  if (wasNearBottom) scrollToBottom();
  else ui["new-reply"].hidden = false;
}

function serviceStatus(text, status, title = "就绪检查不探测上游模型，状态仅供参考。") {
  ui["service-status"].textContent = text;
  ui["service-status"].dataset.state = status;
  ui["service-status"].title = title;
}

async function checkService() {
  if (state.probe) return;
  const probe = new AbortController();
  state.probe = probe;
  serviceStatus("检查中", "checking");
  ui["check-service"].disabled = true;
  // 探针单独限制等待时间；查询本身没有浏览器总超时。
  const timeout = setTimeout(() => probe.abort(), 5000);
  try {
    await checkReadiness(probe.signal);
    serviceStatus("服务与数据库就绪", "ready");
  } catch (error) {
    serviceStatus(error.status === 503 ? "服务暂不可用" : "无法连接或检查失败", "unavailable",
      error.name === "AbortError" ? "就绪检查等待超时，可手动重新检查。" : error.message);
  } finally {
    clearTimeout(timeout);
    if (state.probe === probe) {
      state.probe = null;
      ui["check-service"].disabled = false;
    }
  }
}

function editQuestion(question) {
  if (ui.question.value && ui.question.value !== question && !window.confirm("输入框已有草稿，是否替换为这个问题？")) return;
  ui.question.value = question;
  updateComposer();
  ui.question.focus({ preventScroll: true });
  ui.question.setSelectionRange(question.length, question.length);
}

function current(record) {
  return state.version === record.version && state.active?.id === record.id;
}

function release(record) {
  clearInterval(record.timer);
  if (current(record)) {
    state.active = null;
    updateComposer();
  }
}

function stopWaiting() {
  const record = state.active;
  if (!record) return;
  const follow = nearBottom();
  const focused = record.nodes.card.contains(document.activeElement);
  record.status = "stopped";
  // 先使旧请求失效，即使其响应已到达、正在解析，也不能再更新页面。
  release(record);
  record.controller.abort();
  renderStopped(record.nodes.card, () => editQuestion(record.question));
  afterReply(follow);
  announce("已停止浏览器等待，服务端可能仍在处理。");
  if (focused) ui.question.focus({ preventScroll: true });
}

async function submit(event) {
  event.preventDefault();
  if (state.active || state.composing) return;
  const prepared = prepareQuestion(ui.question.value);
  if (prepared.error) {
    inputError(prepared.error);
    ui.question.focus();
    return;
  }
  const record = {
    id: ++state.nextId, version: state.version, question: prepared.question,
    status: "waiting", controller: new AbortController(), startedAt: performance.now(),
  };
  state.active = record;
  state.records.push(record);
  workspace.classList.add("has-messages");
  record.nodes = createQuery(record, stopWaiting);
  ui.welcome.hidden = true;
  ui.messages.append(record.nodes.user, record.nodes.agent);
  ui["record-count"].textContent = String(state.records.length).padStart(2, "0");
  ui.question.value = "";
  updateComposer();
  scrollToBottom();
  ui.question.focus({ preventScroll: true });
  record.timer = setInterval(() => {
    const seconds = Math.floor((performance.now() - record.startedAt) / 1000);
    record.nodes.elapsed.textContent = `已等待 ${seconds} 秒${seconds >= 30 ? " · 仍在等待服务返回" : ""}`;
  }, 1000);
  try {
    const result = await query(record.question, record.controller.signal);
    if (!current(record)) return;
    const follow = nearBottom();
    const focused = record.nodes.card.contains(document.activeElement);
    record.status = "success";
    record.output = result.output;
    record.requestId = result.requestId;
    renderResult(record.nodes.card, result.output, result.requestId, () => editQuestion(record.question));
    serviceStatus("最近查询成功", "ready");
    afterReply(follow);
    announce("查询已返回，可查看答案、SQL 和证据。");
    if (focused) ui.question.focus({ preventScroll: true });
  } catch (error) {
    if (!current(record)) return;
    const follow = nearBottom();
    const focused = record.nodes.card.contains(document.activeElement);
    record.status = "failed";
    record.error = error instanceof APIError ? error : new APIError("查询未能完成，执行状态未知。请检查服务后重新提交完整问题。");
    renderFailure(record.nodes.card, record.error, () => editQuestion(record.question), checkService);
    if (record.error.status === 503) serviceStatus("服务暂不可用", "unavailable");
    else if (record.error.code === "network_error") serviceStatus("连接中断", "unavailable");
    afterReply(follow);
    announce("本次查询未能完成，可以编辑问题或重新检查服务。");
    if (focused) ui.question.focus({ preventScroll: true });
  } finally {
    // 只清理自己的计时器和锁；停止/清空后启动的新请求不受影响。
    release(record);
  }
}

function clearHistory() {
  if (!state.records.length && !ui.question.value) return;
  if (!window.confirm("清空当前记录和草稿？此操作无法恢复，服务端可能仍在处理已提交的查询。")) return;
  const previous = state.active;
  state.version += 1;
  state.active = null;
  if (previous) {
    clearInterval(previous.timer);
    previous.controller.abort();
  }
  state.records = [];
  workspace.classList.remove("has-messages");
  ui.messages.replaceChildren();
  ui.question.value = "";
  ui.welcome.hidden = false;
  ui["record-count"].textContent = "00";
  ui["new-reply"].hidden = true;
  updateComposer();
  ui.conversation.scrollTop = 0;
  ui.question.focus({ preventScroll: true });
  announce("已清空当前记录和草稿。");
}

ui["query-form"].addEventListener("submit", submit);
ui.question.addEventListener("input", updateComposer);
ui.question.addEventListener("compositionstart", () => { state.composing = true; updateComposer(); });
ui.question.addEventListener("compositionend", () => { state.composing = false; updateComposer(); });
ui.question.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || state.composing || event.keyCode === 229) return;
  event.preventDefault();
  ui["query-form"].requestSubmit();
});
ui["clear-history"].addEventListener("click", clearHistory);
ui["check-service"].addEventListener("click", checkService);
ui["new-reply"].addEventListener("click", scrollToBottom);
ui.conversation.addEventListener("scroll", () => { if (nearBottom()) ui["new-reply"].hidden = true; });
document.querySelectorAll("[data-question]").forEach((node) => {
  node.addEventListener("click", () => editQuestion(node.dataset.question));
});
window.addEventListener("pagehide", () => {
  state.version += 1;
  if (state.active) {
    clearInterval(state.active.timer);
    state.active.controller.abort();
    state.active = null;
  }
  state.probe?.abort();
});
// 浏览器从往返缓存恢复页面时，将不再等待的卡片标记清楚。
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  for (const record of state.records) {
    if (record.status === "waiting") {
      record.status = "stopped";
      renderStopped(record.nodes.card, () => editQuestion(record.question));
    }
  }
  updateComposer();
  void checkService();
});
updateComposer();
void checkService();
