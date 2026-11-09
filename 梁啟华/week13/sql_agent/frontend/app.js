(() => {
  "use strict";

  const API = {
    health: "/api/health",
    chat: "/api/chat",
    session: (id) => "/api/session/" + encodeURIComponent(id),
  };

  const els = {
    chat: document.getElementById("chat"),
    messages: document.getElementById("messages"),
    welcome: document.getElementById("welcome"),
    banner: document.getElementById("banner"),
    statusPill: document.getElementById("status-pill"),
    statusText: document.getElementById("status-text"),
    dbHint: document.getElementById("db-hint"),
    form: document.getElementById("composer"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    newChat: document.getElementById("new-chat"),
    suggestions: document.getElementById("suggestions"),
  };

  let sessionId = null;
  let busy = false;

  // ---------------------------------------------------------------- DOM 工具
  function create(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const CHEVRON_SVG =
    '<svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="m9 18 6-6-6-6"></path></svg>';

  function buildDetails(label, countText, className) {
    const details = create("details", "block" + (className ? " " + className : ""));
    const summary = create("summary");
    summary.innerHTML =
      CHEVRON_SVG +
      '<span class="summary-label"></span>' +
      '<span class="summary-count"></span>';
    summary.querySelector(".summary-label").textContent = label;
    summary.querySelector(".summary-count").textContent = countText || "";
    details.appendChild(summary);
    return details;
  }

  // ---------------------------------------------------------------- 健康检查
  async function loadHealth() {
    setStatus("checking", "连接中");
    try {
      const response = await fetch(API.health);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.status !== "ok") {
        throw data;
      }
      if (data.mock) {
        setStatus("mock", "模拟模式");
        showBanner(
          "当前为离线模拟模式：SQL 与回答由本地规则生成，未调用真实大模型，仅用于界面与接口联调。",
          "warn"
        );
      } else {
        setStatus("ok", "在线");
        hideBanner();
      }
      els.dbHint.textContent =
        data.table_count + " 张表 · " + data.model + (data.mock ? " · mock" : "");
    } catch (error) {
      setStatus("error", "离线");
      const detail =
        (error && (error.error || error.detail)) ||
        "无法连接后端，请确认服务已启动。";
      showBanner("后端不可用：" + detail, "error");
    }
  }

  function setStatus(kind, text) {
    els.statusPill.className = "status-pill status--" + kind;
    els.statusText.textContent = text;
  }

  function showBanner(text, kind) {
    els.banner.textContent = text;
    els.banner.className = "banner" + (kind === "error" ? " banner--error" : "");
  }

  function hideBanner() {
    els.banner.className = "banner hidden";
    els.banner.textContent = "";
  }

  // ---------------------------------------------------------------- 消息渲染
  function appendUser(text) {
    const wrap = create("div", "message user");
    wrap.appendChild(create("div", "bubble", text));
    els.messages.appendChild(wrap);
    scrollToBottom();
  }

  function appendTyping() {
    const wrap = create("div", "message assistant");
    const bubble = create("div", "bubble");
    const head = create("div", "assistant-head");
    head.appendChild(create("span", "assistant-avatar", "S"));
    head.appendChild(create("span", "assistant-name", "SQL Agent"));
    const typing = create("div", "typing");
    typing.setAttribute("aria-label", "正在处理");
    typing.appendChild(create("span"));
    typing.appendChild(create("span"));
    typing.appendChild(create("span"));
    head.appendChild(typing);
    bubble.appendChild(head);
    wrap.appendChild(bubble);
    els.messages.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  function appendAssistant(data) {
    const wrap = create("div", "message assistant");
    const bubble = create("div", "bubble");

    const head = create("div", "assistant-head");
    head.appendChild(create("span", "assistant-avatar", "S"));
    head.appendChild(create("span", "assistant-name", "SQL Agent"));
    const time = create(
      "span",
      "",
      new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    );
    head.appendChild(time);
    bubble.appendChild(head);

    bubble.appendChild(create("div", "answer", data.answer || "（没有返回回答）"));

    if (data.resolved_question && data.resolved_question !== data.question) {
      const resolved = create("div", "resolved");
      resolved.textContent = "结合上下文理解为：" + data.resolved_question;
      bubble.appendChild(resolved);
    }

    bubble.appendChild(buildMeta(data));
    if (Array.isArray(data.trace) && data.trace.length) {
      bubble.appendChild(buildTrace(data.trace));
    }
    if (data.sql) {
      bubble.appendChild(buildSql(data.sql, data.attempts));
    }
    if (Array.isArray(data.columns) && data.columns.length) {
      bubble.appendChild(buildTable(data));
    }

    wrap.appendChild(bubble);
    els.messages.appendChild(wrap);
    scrollToBottom();
  }

  function buildMeta(data) {
    const meta = create("div", "meta");
    if (data.mock) {
      meta.appendChild(create("span", "chip chip--warn", "模拟数据"));
    }
    if (Array.isArray(data.tables) && data.tables.length) {
      meta.appendChild(
        create("span", "chip chip--accent", "表：" + data.tables.join(", "))
      );
    }
    meta.appendChild(
      create("span", "chip", "耗时 " + formatElapsed(data.elapsed_ms))
    );
    meta.appendChild(create("span", "chip", "返回 " + data.row_count + " 行"));
    meta.appendChild(create("span", "chip", "生成 " + data.attempts + " 次"));
    if (data.truncated) {
      meta.appendChild(create("span", "chip chip--warn", "结果已截断"));
    }
    return meta;
  }

  function buildTrace(trace) {
    const details = buildDetails("执行过程", trace.length + " 步");
    const body = create("div", "block-body");
    const steps = create("div", "trace-steps");
    trace.forEach((step, index) => {
      const row = create("div", "step");
      row.appendChild(create("div", "step-index", String(index + 1)));
      const content = create("div", "step-body");
      content.appendChild(create("div", "step-stage", step.stage || "步骤"));
      content.appendChild(
        create("div", "step-detail", step.detail || "")
      );
      row.appendChild(content);
      steps.appendChild(row);
    });
    body.appendChild(steps);
    details.appendChild(body);
    return details;
  }

  function buildSql(sql, attempts) {
    const details = buildDetails("查看 SQL", attempts ? attempts + " 次生成" : "");
    const body = create("div", "block-body");
    const toolbar = create("div", "sql-toolbar");
    const copy = create("button", "copy-button", "复制");
    copy.type = "button";
    copy.addEventListener("click", () => copyText(sql, copy));
    toolbar.appendChild(copy);
    body.appendChild(toolbar);
    body.appendChild(create("pre", "sql-code", sql));
    details.appendChild(body);
    return details;
  }

  function buildTable(data) {
    const details = buildDetails(
      "查询结果",
      data.row_count + " 行" + (data.truncated ? "（已截断）" : "")
    );
    details.open = true;
    const body = create("div", "block-body");

    if (!data.rows || !data.rows.length) {
      body.appendChild(create("div", "empty-result", "没有查询到符合条件的数据。"));
      details.appendChild(body);
      return details;
    }

    const toolbar = create("div", "table-toolbar");
    toolbar.appendChild(
      create(
        "span",
        "",
        "共 " + data.row_count + " 行" + (data.truncated ? "，仅展示前 " + data.rows.length + " 行" : "")
      )
    );
    toolbar.appendChild(create("span", "", data.columns.length + " 个字段"));
    body.appendChild(toolbar);

    const scroller = create("div", "table-scroll");
    const table = create("table", "result-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    data.columns.forEach((column) => {
      headRow.appendChild(create("th", "", String(column)));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    data.rows.forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => {
        const td = document.createElement("td");
        if (cell === null || cell === undefined) {
          td.textContent = "NULL";
          td.className = "cell-null";
        } else if (typeof cell === "number") {
          td.textContent = String(cell);
          td.className = "cell-number";
        } else if (typeof cell === "object") {
          td.textContent = JSON.stringify(cell);
        } else {
          td.textContent = String(cell);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    scroller.appendChild(table);
    body.appendChild(scroller);
    details.appendChild(body);
    return details;
  }

  function appendError(message, code) {
    const wrap = create("div", "message assistant");
    const bubble = create("div", "bubble error-bubble");
    const head = create("div", "assistant-head");
    head.appendChild(create("span", "assistant-avatar", "!"));
    head.appendChild(create("span", "assistant-name", "请求失败"));
    bubble.appendChild(head);

    const title = create("div", "error-title", "无法完成本次查询");
    bubble.appendChild(title);
    bubble.appendChild(create("div", "error-detail", message));

    const hint = errorHint(code);
    if (hint) {
      bubble.appendChild(create("div", "resolved", hint));
    }

    wrap.appendChild(bubble);
    els.messages.appendChild(wrap);
    scrollToBottom();
  }

  function errorHint(code) {
    switch (code) {
      case "llm_error":
        return "请检查 OPENAI_API_KEY、OPENAI_BASE_URL 与 SQL_AGENT_MODEL 是否可用。";
      case "agent_unavailable":
        return "请检查数据库路径 SQL_AGENT_DB_PATH 与大模型配置后重启服务。";
      case "timeout":
        return "模型或数据库响应过慢，可尝试更简单的问题，或调大 SQL_AGENT_TIMEOUT。";
      case "sql_agent_error":
        return "SQL 经过多轮改写仍未通过校验/执行，可换一种问法或调大 SQL_AGENT_MAX_REWRITES。";
      case "database_error":
        return "数据库执行出错，请检查数据库文件是否只读、表结构是否完整。";
      case "network_error":
        return "请确认后端进程仍在运行，并检查浏览器访问地址与端口是否正确。";
      default:
        return "";
    }
  }

  function formatElapsed(ms) {
    if (typeof ms !== "number" || Number.isNaN(ms)) return "-";
    if (ms < 1000) return Math.round(ms) + " ms";
    return (ms / 1000).toFixed(1) + " s";
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      els.chat.scrollTop = els.chat.scrollHeight;
    });
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const original = button.textContent;
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = original;
      }, 1200);
    } catch (error) {
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      document.body.removeChild(helper);
      button.textContent = "已复制";
    }
  }

  // ---------------------------------------------------------------- 发送逻辑
  function setBusy(value) {
    busy = value;
    els.send.disabled = value;
    els.input.disabled = value;
  }

  async function send(text) {
    const message = text.trim();
    if (!message || busy) return;

    els.welcome.classList.add("hidden");
    appendUser(message);
    const typing = appendTyping();
    setBusy(true);

    try {
      const response = await fetch(API.chat, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw { payload, status: response.status };
      }
      if (payload.session_id) {
        sessionId = payload.session_id;
      }
      typing.remove();
      appendAssistant(payload);
    } catch (error) {
      typing.remove();
      if (!error.status) {
        appendError(
          "网络请求失败：无法连接后端，请确认服务已启动后重试。",
          "network_error"
        );
      } else {
        const detail = describeError(error.payload || {}, error.status);
        appendError(detail.message, detail.code);
      }
    } finally {
      setBusy(false);
      els.input.focus();
    }
  }

  function describeError(payload, status) {
    let detail = payload.detail;
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => item.msg || JSON.stringify(item))
        .join("；");
    }
    return {
      message: detail ? String(detail) : "请求失败（HTTP " + (status || "未知") + "）",
      code: payload.code || "",
    };
  }

  function autoresize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 160) + "px";
  }

  function resetConversation() {
    if (sessionId) {
      fetch(API.session(sessionId), { method: "DELETE" }).catch(() => {});
    }
    sessionId = null;
    els.messages.textContent = "";
    els.welcome.classList.remove("hidden");
    els.input.value = "";
    autoresize();
    els.input.focus();
  }

  // ---------------------------------------------------------------- 事件绑定
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = els.input.value;
    els.input.value = "";
    autoresize();
    send(text);
  });

  els.input.addEventListener("input", autoresize);

  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      els.form.requestSubmit();
    }
  });

  els.suggestions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-question]");
    if (button) send(button.dataset.question || "");
  });

  els.newChat.addEventListener("click", resetConversation);

  loadHealth();
  els.input.focus();
})();
