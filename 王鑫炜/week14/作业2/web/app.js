const $ = (selector) => document.querySelector(selector);
const state = {
  docs: [],
  selected: new Set(),
  messages: [],
  busy: false,
  health: {},
  image: null,
  preview: null,
  uploading: false,
};
async function api(path, options = {}) {
  const response = await fetch(path, options);
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(`服务响应异常（${response.status}）`);
  }
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string" ? result.detail : "请求格式不正确",
    );
  return result;
}
function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  setTimeout(() => {
    $("#toast").hidden = true;
  }, 3500);
}
function formatTime(value) {
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
}
function renderDocs() {
  const list = $("#document-list");
  const focused = document.activeElement?.dataset?.doc;
  list.replaceChildren();
  if (!state.docs.length) {
    list.append(node("p", "selection-note", "还没有文档，先添加一份资料吧。"));
  }
  for (const doc of state.docs) {
    const card = node(
      "div",
      "document-card" + (state.selected.has(doc.id) ? " checked" : ""),
    );
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(doc.id);
    checkbox.disabled = doc.status !== "completed";
    checkbox.dataset.doc = doc.id;
    checkbox.setAttribute("aria-label", "选择文档 " + doc.name);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(doc.id);
      else state.selected.delete(doc.id);
      renderDocs();
    });
    const content = node("div", "doc-content");
    content.append(node("b", "", doc.name));
    const meta = node("div", "doc-meta");
    meta.append(
      node("span", "status " + doc.status, doc.status_label),
      node("span", "", doc.pages + " 页"),
    );
    if (doc.status === "completed")
      meta.append(node("span", "", doc.image_count + " 张图"));
    content.append(meta);
    if (doc.error) {
      content.append(node("p", "doc-error", doc.error));
      const retry = node("button", "retry", "重新解析");
      retry.addEventListener("click", async () => {
        try {
          await api(`/api/documents/${doc.id}/retry`, { method: "POST" });
          await loadDocs();
        } catch (e) {
          toast(e.message);
        }
      });
      content.append(retry);
    }
    card.append(checkbox, content);
    list.append(card);
  }
  if (focused) document.querySelector(`[data-doc="${focused}"]`)?.focus();
  $("#doc-total").textContent = state.docs.length + " 份";
  for (const status of ["queued", "processing", "completed", "failed"])
    $(`#${status}-count`).textContent = state.docs.filter(
      (x) => x.status === status,
    ).length;
  $("#failed-summary").hidden = !state.docs.some((x) => x.status === "failed");
  const count = state.docs.filter((x) => x.status === "completed").length;
  $("#selection-note").textContent = state.selected.size
    ? `已选择 ${state.selected.size} 份文档参与问答`
    : "未勾选时，将检索全部已完成文档";
  $("#scope-label").textContent = state.selected.size
    ? `已选择 ${state.selected.size} 份文档`
    : "全部已完成文档";
  $("#summary-caption").textContent = count
    ? `共 ${state.docs.reduce((a, b) => a + b.chunk_count, 0)} 个知识片段`
    : "等待文档完成解析";
}
async function loadDocs() {
  const data = await api("/api/documents");
  const before = JSON.stringify(state.docs);
  state.docs = data.documents;
  if (
    JSON.stringify(state.docs) !== before ||
    !$("#document-list").children.length
  )
    renderDocs();
}
async function upload(files) {
  if (state.uploading) return;
  if (!files.length) return;
  state.uploading = true;
  $("#upload-error").textContent = "";
  $("#upload-label").textContent = "正在上传…";
  try {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const result = await api("/api/documents", { method: "POST", body: form });
    toast(`${result.documents.length} 份文档已加入解析队列`);
    await loadDocs();
  } catch (e) {
    $("#upload-error").textContent = e.message;
  } finally {
    state.uploading = false;
    $("#upload-label").textContent = "添加 PDF 文档";
    $("#pdf-input").value = "";
  }
}
function renderMessage(item) {
  const wrapper = node(
    "article",
    "message " + (item.role === "user" ? "user-message" : "assistant-message"),
  );
  if (item.role === "user") {
    const bubble = node("div", "user-bubble");
    if (item.image_url) {
      const img = document.createElement("img");
      img.src = item.image_url;
      img.alt = "本轮提问附图";
      bubble.append(img);
    }
    bubble.append(node("span", "", item.text));
    wrapper.append(bubble);
    return wrapper;
  }
  const header = node("div", "assistant-header");
  header.append(
    node("span", "assistant-mark", "知"),
    node("b", "", "知页"),
    node("small", "", item.model),
  );
  const body = node("div", "answer-body");
  for (const block of item.blocks || [{ type: "text", text: item.text }]) {
    if (block.type === "text") body.append(node("p", "", block.text));
    else if (block.type === "image") {
      const figure = document.createElement("figure");
      const img = document.createElement("img");
      img.src = block.url;
      img.alt = block.caption || "文档插图";
      img.loading = "lazy";
      figure.append(img, node("figcaption", "", block.caption));
      body.append(figure);
    }
  }
  if (item.sources?.length) {
    const sources = node("div", "source-list");
    item.sources.forEach((source, index) => {
      const link = node(
        "a",
        "",
        `来源${index + 1} · ${source.name} · P${source.page}`,
      );
      link.href = `/api/documents/${source.doc_id}/pdf#page=${source.page}`;
      link.target = "_blank";
      link.rel = "noopener";
      sources.append(link);
    });
    body.append(sources);
  }
  body.append(
    node(
      "div",
      "answer-meta",
      `对话已保存至 Redis · ${(item.elapsed_ms / 1000).toFixed(1)} 秒 · ${item.image_input_count} 张图参与本轮回答`,
    ),
  );
  wrapper.append(header, body);
  return wrapper;
}
function renderHistory() {
  document.body.classList.toggle("has-messages", state.messages.length > 0);
  $("#empty-chat").hidden = !!state.messages.length;
  $("#messages").replaceChildren(...state.messages.map(renderMessage));
}
async function loadHistory() {
  try {
    const data = await api("/api/history");
    state.messages = data.messages;
    renderHistory();
  } catch (e) {
    $("#chat-error").textContent = e.message;
  }
}
async function loadHealth() {
  try {
    state.health = await api("/api/health");
    const ok = state.health.redis && state.health.kafka && state.health.worker;
    $("#system-dot").classList.toggle("online", ok);
    $("#system-status").textContent = ok ? "解析服务已连接" : "服务配置待完成";
    $("#model-badge").textContent =
      state.health.model + (state.health.llm ? " · 视觉问答" : " · 待配置");
    if (!$("#logs-panel").hidden) renderServices();
  } catch (e) {
    $("#system-status").textContent = "后端连接中断";
    $("#system-dot").classList.remove("online");
  }
}
function renderServices() {
  const row = $("#service-row");
  row.replaceChildren();
  for (const [key, label] of [
    ["redis", "Redis"],
    ["kafka", "Kafka"],
    ["worker", "解析 Worker"],
    ["llm", "视觉模型"],
  ])
    row.append(
      node("span", "", `${label} · ${state.health[key] ? (key === "llm" ? "已配置" : "已连接") : "未就绪"}`),
    );
  row.append(node("span", "", `Topic · ${state.health.topic || "—"}`));
}
async function loadLogs() {
  try {
    const data = await api("/api/events");
    renderServices();
    const list = $("#event-list");
    list.replaceChildren();
    const labels = {
      queued: "待解析",
      kafka_published: "Kafka 已投递",
      processing: "解析中",
      completed: "解析完成",
      failed: "解析失败",
      offset_committed: "Offset 已提交",
    };
    for (const event of data.events) {
      const el = node("div", "event");
      const time = node("time", "", formatTime(event.created_at));
      const detail = node("div", "", event.name || event.doc_id);
      detail.append(node("span", "event-detail", event.detail));
      el.append(
        time,
        node("b", "", labels[event.event] || event.event),
        detail,
      );
      list.append(el);
    }
    if (!data.events.length)
      list.append(
        node("p", "log-help", "上传 PDF 后，这里会出现真实运行记录。"),
      );
  } catch (e) {
    toast(e.message);
  }
}
function removeImage() {
  state.image = null;
  if (state.preview) URL.revokeObjectURL(state.preview);
  state.preview = null;
  $("#attachment-preview").replaceChildren();
  $("#attachment-preview").hidden = true;
  $("#image-input").value = "";
}
$("#image-input").addEventListener("change", () => {
  const file = $("#image-input").files[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {
    toast("图片不得超过8MB");
    $("#image-input").value = "";
    return;
  }
  removeImage();
  state.image = file;
  state.preview = URL.createObjectURL(file);
  const row = node("div", "attachment");
  const img = document.createElement("img");
  img.src = state.preview;
  img.alt = "待发送图片预览";
  const button = node("button", "", "×");
  button.type = "button";
  button.setAttribute("aria-label", "移除附图");
  button.addEventListener("click", removeImage);
  row.append(img, node("span", "", file.name), button);
  $("#attachment-preview").append(row);
  $("#attachment-preview").hidden = false;
});
$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const question = $("#question").value.trim();
  if (!question) return;
  $("#chat-error").textContent = "";
  state.busy = true;
  $("#send-button").disabled = true;
  $("#send-button").textContent = "回答中…";
  const pending = node("div", "pending");
  pending.append(
    node("span", "spinner"),
    node("span", "", "正在检索资料并生成回答…"),
  );
  $("#empty-chat").hidden = true;
  $("#messages").append(pending);
  try {
    const form = new FormData();
    form.append("question", question);
    form.append("document_ids", JSON.stringify([...state.selected]));
    if (state.image) form.append("image", state.image);
    const data = await api("/api/chat", { method: "POST", body: form });
    state.messages.push(data.user, data.answer);
    $("#question").value = "";
    removeImage();
    renderHistory();
    $("#conversation").scrollTop = $("#conversation").scrollHeight;
    $("#question").focus();
    toast("回答与历史对话已保存");
  } catch (e) {
    $("#chat-error").textContent = e.message;
    renderHistory();
  } finally {
    pending.remove();
    state.busy = false;
    $("#send-button").disabled = false;
    $("#send-button").textContent = "发送问题 ↗";
  }
});
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#chat-form").requestSubmit();
  }
});
$("#pdf-input").addEventListener("change", () => upload($("#pdf-input").files));
const drop = $("#dropzone");
drop.addEventListener("dragover", (e) => {
  e.preventDefault();
  drop.classList.add("dragging");
});
drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("dragging");
  upload(e.dataTransfer.files);
});
for (const label of document.querySelectorAll("label[tabindex]"))
  label.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      document.getElementById(label.htmlFor).click();
    }
  });
for (const button of document.querySelectorAll("[data-question]"))
  button.addEventListener("click", () => {
    $("#question").value = button.dataset.question;
    $("#question").focus();
  });
$("#logs-toggle").addEventListener("click", async () => {
  const panel = $("#logs-panel");
  panel.hidden = !panel.hidden;
  $("#logs-toggle").setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) await loadLogs();
});
$("#refresh-logs").addEventListener("click", () =>
  Promise.all([loadLogs(), loadHealth()]),
);
$("#new-session").addEventListener("click", async () => {
  if (state.busy) {
    toast("请等待本轮完成后再创建对话");
    return;
  }
  try {
    await api("/api/session", { method: "POST" });
    state.messages = [];
    renderHistory();
    removeImage();
    $("#chat-error").textContent = "";
    toast("新对话已创建，旧对话仍保留在 Redis");
  } catch (e) {
    toast(e.message);
  }
});
Promise.all([loadDocs(), loadHealth(), loadHistory()]).catch((e) => {
  $("#upload-error").textContent = e.message;
});
setInterval(() => {
  if (!document.hidden) {
    loadDocs().catch(() => {});
    if (!$("#logs-panel").hidden) loadLogs();
  }
}, 2000);
setInterval(() => {
  if (!document.hidden) loadHealth();
}, 15000);
