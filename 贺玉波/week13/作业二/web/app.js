/* LangGraph SQL Agent - 前端：把后端 trace 渲染成可展开的执行步骤。 */

const els = {
  chat: document.getElementById("chat"),
  empty: document.getElementById("emptyState"),
  examples: document.getElementById("examples"),
  question: document.getElementById("question"),
  sendBtn: document.getElementById("sendBtn"),
  clearBtn: document.getElementById("clearBtn"),
  mockToggle: document.getElementById("mockToggle"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  hint: document.getElementById("hint"),
};

const state = {
  busy: false,
  mock: false,
  keyConfigured: false,
  history: [],
};

const EXAMPLES = [
  "总共有多少张专辑？",
  "销售额最高的前 10 笔发票是哪些？",
  "列出前 10 位客户及其所在国家",
  "最长的 10 首歌曲是哪些？",
];

const NODE_LABEL = {
  select_tables: "选表",
  generate_sql: "生成",
  validate_sql: "校验",
  execute_sql: "执行",
  repair_sql: "修复",
  summarize: "汇总",
  clarify: "澄清",
  fail: "兜底",
};

/* ---------------- 初始化 ---------------- */

function initExamples() {
  const frag = document.createDocumentFragment();
  EXAMPLES.forEach((text) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      els.question.value = text;
      autoResize();
      send(text);
    });
    frag.appendChild(chip);
  });
  els.examples.appendChild(frag);
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const info = await res.json();
    state.keyConfigured = !!info.key_configured;
    state.mock = !!info.mock_default;
    els.mockToggle.checked = state.mock;
    els.statusDot.className = "dot ok";
    els.statusText.textContent = `已连接 · ${info.model}`;
    if (!info.db_exists) {
      els.statusDot.className = "dot bad";
      els.statusText.textContent = "数据库文件缺失";
    }
    if (!state.keyConfigured) {
      state.mock = true;
      els.mockToggle.checked = true;
      setHint("未检测到 DEEPSEEK_API_KEY，已自动开启离线演示（真实执行 SQLite，Mock 负责模型决策）。", true);
    } else {
      setHint(mockHint());
    }
  } catch (err) {
    els.statusDot.className = "dot bad";
    els.statusText.textContent = "后端未连接";
    setHint("无法连接后端，请先运行 python -m uvicorn server.main:app --port 8000", true);
  }
}

function mockHint() {
  return state.mock
    ? "离线演示：真实执行 SQLite 校验/查询，Mock 规则替代模型决策。"
    : "在线模式：由后端 .env 中的 DeepSeek 密钥调用模型。";
}

function setHint(text, warn = false) {
  els.hint.textContent = text;
  els.hint.classList.toggle("warn", warn);
}

/* ---------------- 发送 ---------------- */

async function send(text, { recordUser = true, retryCard = null } = {}) {
  const question = (text || "").trim();
  if (!question || state.busy) return;

  if (retryCard) retryCard.remove();
  if (els.empty) els.empty.hidden = true;
  if (recordUser) addUser(question);
  els.question.value = "";
  autoResize();

  const shell = createAssistantShell();
  els.chat.appendChild(shell.wrap);
  shell.card.innerHTML = `
    <div class="loading-card">
      <span class="spinner"></span>
      <span>正在执行 LangGraph 节点…</span>
    </div>`;
  setBusy(true);
  scrollToBottom();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        history: state.history.slice(-3),
        mock: state.mock,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const message =
        (data.error && data.error.message) || data.detail || `请求失败（HTTP ${res.status}）`;
      throw new Error(message);
    }
    if (data.status === "interrupted") {
      renderClarification(shell.card, data, question);
      return;
    }
    shell.card.innerHTML = renderAssistant(data);
    bindCopyButtons(shell.card);
    if (!data.result.error) {
      state.history.push({ question, answer: data.result.answer || "" });
    }
  } catch (err) {
    shell.card.classList.add("error-card");
    shell.card.innerHTML = `
      <div class="answer-head">
        <h3>执行失败</h3>
        <span class="badge err">请求未完成</span>
      </div>
      <p class="error-text">${escapeHtml(err.message || "未知错误")}</p>
      <button class="retry" type="button">重试</button>`;
    shell.card.querySelector(".retry").addEventListener("click", () => {
      send(question, { recordUser: false, retryCard: shell.wrap });
    });
  } finally {
    setBusy(false);
    scrollToBottom();
  }
}

function addUser(text) {
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `<div class="bubble-user">${escapeHtml(text)}</div>`;
  els.chat.appendChild(wrap);
}

function createAssistantShell() {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="avatar">LG</div><article class="answer-card"></article>`;
  return { wrap, card: wrap.querySelector(".answer-card") };
}

/* ---------------- 渲染 ---------------- */

function renderAssistant(data) {
  const result = data.result || {};
  const trace = data.trace || [];
  const meta = data.meta || {};
  const failed = !!result.error;

  const badges = [
    `<span class="badge ${failed ? "err" : "ok"}">${failed ? "未完成" : "完成"}</span>`,
    `<span class="badge">${escapeHtml(meta.provider === "mock" ? "Mock 离线" : "DeepSeek")}</span>`,
    result.tables && result.tables.length
      ? `<span class="badge">表：${escapeHtml(result.tables.join("、"))}</span>`
      : "",
    `<span class="badge">${Number(result.row_count || 0)} 行</span>`,
    Number(result.repair_attempts || 0) > 0
      ? `<span class="badge warn">修复 ${result.repair_attempts} 次</span>`
      : "",
    meta.elapsed_ms ? `<span class="badge">${Math.round(meta.elapsed_ms / 1000 * 10) / 10}s</span>` : "",
  ]
    .filter(Boolean)
    .join("");

  const caveats = (result.caveats || []).filter(Boolean);
  const caveatBlock = caveats.length
    ? `<div class="caveats"><ul>${caveats.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>`
    : "";

  const steps = trace.length
    ? `<h4 class="section-title">执行步骤（${trace.length} 个节点）</h4>
       <div class="timeline">${trace.map(renderStep).join("")}</div>`
    : "";

  const sqlBlock = result.sql
    ? `<h4 class="section-title">最终 SQL</h4>
       <div class="sql-block">
         <div class="sql-tools">
           <span class="badge">${result.tables && result.tables.length ? escapeHtml(result.tables.join(", ")) : "SQL"}</span>
           <button class="copy" type="button">复制</button>
         </div>
         <pre class="code">${escapeHtml(result.sql)}</pre>
       </div>`
    : "";

  const tableBlock = data.result_table ? renderTable(data.result_table) : "";

  return `
    <div class="answer-head">
      <h3>${failed ? "未得到可用答案" : "回答"}</h3>
      <div class="badges">${badges}</div>
    </div>
    <p class="answer-text">${escapeHtml(result.answer || "（无答案）")}</p>
    ${caveatBlock}
    ${steps}
    ${sqlBlock}
    ${tableBlock}`;
}

/* ---------------- Human-in-the-loop ---------------- */

function renderClarification(card, data, question) {
  const info = data.interrupt || {};
  const trace = data.trace || [];
  const meta = data.meta || {};
  const options = info.options || [];
  const tables = (data.result && data.result.tables) || [];

  const badges = [
    `<span class="badge warn">等待澄清</span>`,
    `<span class="badge">${escapeHtml(meta.provider === "mock" ? "Mock 离线" : "DeepSeek")}</span>`,
    tables.length ? `<span class="badge">表：${escapeHtml(tables.join("、"))}</span>` : "",
    meta.elapsed_ms ? `<span class="badge">${Math.round(meta.elapsed_ms / 1000 * 10) / 10}s</span>` : "",
  ].filter(Boolean).join("");

  const optionButtons = options.length
    ? `<div class="clarify-options">${options
        .map((opt, index) => `<button class="clarify-opt" type="button" data-index="${index}">${escapeHtml(opt)}</button>`)
        .join("")}</div>`
    : "";

  card.innerHTML = `
    <div class="answer-head">
      <h3>需要补充信息</h3>
      <div class="badges">${badges}</div>
    </div>
    <p class="answer-text">${escapeHtml(info.question || "模型无法确定回答口径，请补充说明。")}</p>
    ${optionButtons}
    <form class="clarify-form">
      <textarea class="clarify-input" rows="2" placeholder="选择上面的口径，或直接输入你的回答…"></textarea>
      <button class="clarify-submit" type="submit">继续执行</button>
    </form>
    <p class="clarify-note">图已暂停在 clarify 节点；提交后从同一个检查点恢复，不会重新选表。</p>
    ${trace.length ? `<h4 class="section-title">执行步骤（${trace.length} 个节点）</h4>
       <div class="timeline">${trace.map(renderStep).join("")}</div>` : ""}`;

  const input = card.querySelector(".clarify-input");

  const submitAnswer = (value) => {
    const answer = String(value || "").trim();
    if (!answer || state.busy) {
      if (!answer) input.focus();
      return;
    }
    resumeClarification(card, data, question, answer);
  };

  card.querySelectorAll(".clarify-opt").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.busy) return;
      card.querySelectorAll(".clarify-opt").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      input.value = btn.textContent;
      submitAnswer(btn.textContent);
    });
  });

  card.querySelector(".clarify-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitAnswer(input.value);
  });
  scrollToBottom();
}

async function resumeClarification(card, data, question, answer) {
  if (state.busy) return;
  card.classList.remove("error-card");
  card.innerHTML = `
    <div class="loading-card">
      <span class="spinner"></span>
      <span>已收到澄清，正在继续执行后续节点…</span>
    </div>`;
  setBusy(true);
  scrollToBottom();

  try {
    const res = await fetch("/api/chat/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: data.thread_id, answer, mock: state.mock }),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok || !out.ok) {
      const message =
        (out.error && out.error.message) || out.detail || `恢复失败（HTTP ${res.status}）`;
      throw new Error(message);
    }
    card.innerHTML = renderAssistant(out);
    bindCopyButtons(card);
    if (!out.result.error) {
      state.history.push({ question, answer: out.result.answer || "" });
    }
  } catch (err) {
    card.classList.add("error-card");
    card.innerHTML = `
      <div class="answer-head">
        <h3>恢复执行失败</h3>
        <span class="badge err">请求未完成</span>
      </div>
      <p class="error-text">${escapeHtml(err.message || "未知错误")}</p>
      <button class="retry" type="button">重试</button>`;
    card.querySelector(".retry").addEventListener("click", () => {
      resumeClarification(card, data, question, answer);
    });
  } finally {
    setBusy(false);
    scrollToBottom();
  }
}

function renderStep(step) {
  const detail = step.detail || {};
  const status = ["ok", "warn", "error"].includes(step.status) ? step.status : "ok";
  const mark = status === "ok" ? "✓" : status === "warn" ? "!" : "×";
  const label = NODE_LABEL[step.node] || step.node;
  const summary = summarizeStep(step);

  const extra = [];
  if (detail.sql) {
    extra.push(`<pre class="code">${escapeHtml(detail.sql)}</pre>`);
  }
  extra.push(`<pre class="json">${escapeHtml(JSON.stringify(detail, null, 2))}</pre>`);

  return `
    <div class="step ${status}">
      <div class="step-dot">${mark}</div>
      <div class="step-body">
        <div class="step-head">
          <span class="step-title">${escapeHtml(step.title || label)}
            <span class="step-node">${escapeHtml(step.node || "")}</span>
          </span>
          <span class="step-time">${Number(step.elapsed_ms || 0)} ms</span>
        </div>
        <div class="step-sum">${summary}</div>
        <details class="fold">
          <summary>节点详情</summary>
          ${extra.join("")}
        </details>
      </div>
    </div>`;
}

function summarizeStep(step) {
  const d = step.detail || {};
  switch (step.node) {
    case "select_tables":
      if (d.needs_clarification) return "模型认为问题需要澄清";
      if (d.problems && d.problems.length) return `选表无效：${escapeHtml(d.problems.join("；"))}`;
      return `选中 <code>${escapeHtml((d.tables || []).join("、") || "无")}</code>，置信度 ${Math.round((d.confidence || 0) * 100)}%`;
    case "generate_sql":
      return `已生成 SQL：<code>${escapeHtml(shorten(d.sql, 90))}</code>`;
    case "validate_sql":
      return d.ok
        ? "安全门禁 + EXPLAIN 编译通过"
        : `校验失败（${escapeHtml(d.code || "error")}）：${escapeHtml(shorten(d.message, 140))}`;
    case "execute_sql":
      return d.ok
        ? `返回 ${Number(d.row_count || 0)} 行${d.truncated ? "（结果已截断）" : ""}`
        : `执行失败：${escapeHtml(shorten(d.message, 140))}`;
    case "repair_sql":
      return d.cannot_fix
        ? `无法修复：${escapeHtml(shorten(d.reason, 140))}`
        : `第 ${d.attempt}/${d.max_attempts} 次修复：<code>${escapeHtml(shorten(d.sql, 90))}</code>`;
    case "summarize":
      return d.fallback
        ? "模型汇总失败，已降级为确定性表格输出"
        : `已生成自然语言答案${d.provider === "mock" ? "（Mock）" : ""}`;
    case "clarify":
      if (d.waiting) return `等待人工澄清：${escapeHtml(shorten(d.question, 160))}`;
      return `已收到澄清：${escapeHtml(shorten(d.answer, 120))}`;
    case "fail":
      return escapeHtml(shorten(d.message, 160));
    default:
      return escapeHtml(step.node || "");
  }
}

function renderTable(table) {
  const columns = table.columns || [];
  const rows = table.rows || [];
  if (!columns.length) return "";
  const MAX_ROWS = 60;
  const shown = rows.slice(0, MAX_ROWS);
  const head = `<tr>${columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
  const body = shown
    .map((row) => `<tr>${row.map((cell) => `<td title="${escapeHtml(cell)}">${escapeHtml(cell)}</td>`).join("")}</tr>`)
    .join("");
  const note =
    rows.length > MAX_ROWS || table.truncated
      ? `<p class="table-note">共 ${Number(table.row_count || rows.length)} 行，页面最多展示 ${MAX_ROWS} 行${table.truncated ? "（服务端已截断）" : ""}。</p>`
      : "";

  return `
    <h4 class="section-title">结果集</h4>
    <details class="fold" open>
      <summary>展开 / 收起结果表（${Number(table.row_count || rows.length)} 行）</summary>
      <div class="table-wrap">
        <table class="result"><thead>${head}</thead><tbody>${body}</tbody></table>
      </div>
      ${note}
    </details>`;
}

function bindCopyButtons(root) {
  root.querySelectorAll(".sql-block").forEach((block) => {
    const btn = block.querySelector(".copy");
    const pre = block.querySelector("pre.code");
    if (!btn || !pre) return;
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(pre.textContent);
        btn.textContent = "已复制";
      } catch {
        btn.textContent = "复制失败";
      }
      setTimeout(() => (btn.textContent = "复制"), 1500);
    });
  });
}

/* ---------------- 工具 ---------------- */

function shorten(text, limit) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > limit ? value.slice(0, limit) + "…" : value;
}

function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function setBusy(busy) {
  state.busy = busy;
  els.sendBtn.disabled = busy;
  els.sendBtn.querySelector(".send-label").textContent = busy ? "执行中" : "发送";
  els.sendBtn.querySelector(".send-spinner").hidden = !busy;
}

function autoResize() {
  els.question.style.height = "auto";
  els.question.style.height = `${Math.min(els.question.scrollHeight, 140)}px`;
}

function scrollToBottom() {
  els.chat.scrollTop = els.chat.scrollHeight;
}

function clearChat() {
  Array.from(els.chat.children).forEach((child) => {
    if (child !== els.empty) child.remove();
  });
  if (els.empty) els.empty.hidden = false;
  state.history = [];
  setHint(state.keyConfigured ? mockHint() : "未检测到 DEEPSEEK_API_KEY，已自动开启离线演示。", !state.keyConfigured);
}

/* ---------------- 事件 ---------------- */

els.sendBtn.addEventListener("click", () => send(els.question.value));
els.clearBtn.addEventListener("click", clearChat);
els.question.addEventListener("input", autoResize);
els.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send(els.question.value);
  }
});
els.mockToggle.addEventListener("change", () => {
  state.mock = els.mockToggle.checked;
  setHint(mockHint(), false);
});

initExamples();
checkHealth();
autoResize();
