/* 情感陪伴助手 —— 前端进程：接收用户输入，调用 FastAPI 后端并展示结果。 */

const WORKFLOW_LABELS = {
  agents: "OpenAI Agents",
  langgraph: "LangGraph",
};

const SENTIMENT_LABELS = {
  positive: "😊 正向",
  negative: "😔 负面",
  neutral: "😐 中性",
};

const STRATEGY_LABELS = {
  encourage: "鼓励",
  comfort: "排解",
  guide: "引导",
};

const els = {
  backend: document.querySelector("#backend"),
  clear: document.querySelector("#clear"),
  status: document.querySelector("#status"),
  messages: document.querySelector("#messages"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#input"),
  send: document.querySelector("#send"),
};

const state = {
  busy: false,
  enabled: [],
};

function setStatus(ok, text) {
  els.status.className = `status ${ok ? "ok" : "error"}`;
  els.status.textContent = text;
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function createMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "🧑" : "🤖";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (text) {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    bubble.appendChild(paragraph);
  }

  article.append(avatar, bubble);
  els.messages.appendChild(article);
  scrollToBottom();
  return { article, bubble };
}

function addWelcome() {
  const { bubble } = createMessage(
    "assistant",
    "你好，我是情感陪伴助手。说说你现在的心情吧，我会先识别情绪，再决定鼓励你，还是陪你一起排解。",
  );
  bubble.classList.add("welcome");
}

function addTyping() {
  const { article, bubble } = createMessage("assistant", "");
  article.classList.add("typing");
  for (let i = 0; i < 3; i += 1) {
    bubble.appendChild(document.createElement("i"));
  }
  scrollToBottom();
  return article;
}

function addMeta(bubble, data) {
  const sentiment = data.sentiment || {};
  const label = SENTIMENT_LABELS[sentiment.label] || sentiment.label || "未知";
  const strategy = STRATEGY_LABELS[data.strategy] || data.strategy || "未知";
  const confidence = Math.round(Math.max(0, Math.min(1, Number(sentiment.confidence) || 0)) * 100);

  const meta = document.createElement("div");
  meta.className = "meta";

  const badge = document.createElement("span");
  badge.className = `badge ${sentiment.label || "neutral"}`;
  badge.textContent = label;
  badge.title = sentiment.reason || "";
  meta.appendChild(badge);

  const items = [
    `情绪：${sentiment.emotion || "未知"}`,
    `置信度 ${confidence}%`,
    `策略：${strategy}`,
    `${data.backend || "未知"} · ${data.model || "未知"}`,
  ];

  for (const item of items) {
    const span = document.createElement("span");
    span.textContent = item;
    meta.appendChild(span);
  }

  bubble.appendChild(meta);
  scrollToBottom();
}

function addAssistant(data) {
  const { bubble } = createMessage("assistant", data.reply || "（后端没有返回内容）");
  addMeta(bubble, data);
}

function addError(text) {
  const { bubble } = createMessage("assistant", text);
  bubble.classList.add("error");
}

function syncBackendOptions(defaultWorkflow) {
  const enabled = new Set(state.enabled);

  for (const option of Array.from(els.backend.options)) {
    option.disabled = enabled.size > 0 && !enabled.has(option.value);
  }

  if (defaultWorkflow && enabled.has(defaultWorkflow)) {
    els.backend.value = defaultWorkflow;
  } else if (state.enabled.length > 0) {
    els.backend.value = state.enabled[0];
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    state.enabled = Array.isArray(data.workflows) ? data.workflows : [];
    syncBackendOptions(data.default_workflow);

    const names = state.enabled
      .map((name) => WORKFLOW_LABELS[name] || name)
      .join(" / ");
    const suffix = data.configured ? "" : "（尚未配置 API Key）";
    setStatus(true, `服务正常 · 模型 ${data.model} · 已启用：${names}${suffix}`);
  } catch (error) {
    setStatus(false, "无法连接后端，请确认已运行 uvicorn app.main:app");
  }
}

async function sendMessage(message) {
  if (state.busy) {
    return;
  }

  state.busy = true;
  els.send.disabled = true;
  els.input.disabled = true;

  createMessage("user", message);
  const typing = addTyping();

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 120000);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        backend: els.backend.value,
      }),
      signal: controller.signal,
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    if (!data.reply) {
      throw new Error("后端返回内容为空");
    }

    typing.remove();
    addAssistant(data);
  } catch (error) {
    typing.remove();
    const text =
      error.name === "AbortError"
        ? "请求超时，请稍后重试。"
        : `调用失败：${error.message}`;
    addError(text);
  } finally {
    window.clearTimeout(timer);
    state.busy = false;
    els.send.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
}

function resizeInput() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 140)}px`;
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = els.input.value.trim();
  if (!message || state.busy) {
    return;
  }
  els.input.value = "";
  resizeInput();
  sendMessage(message);
});

els.input.addEventListener("input", resizeInput);

els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

els.clear.addEventListener("click", () => {
  els.messages.innerHTML = "";
  addWelcome();
  els.input.focus();
});

addWelcome();
resizeInput();
checkHealth();
els.input.focus();
