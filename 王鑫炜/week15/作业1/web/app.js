const form = document.querySelector("#emotion-form");
const text = document.querySelector("#text");
const framework = document.querySelector("#framework");
const submit = document.querySelector("#submit");
const error = document.querySelector("#error");
const status = document.querySelector("#status");
const sentiment = document.querySelector("#sentiment");
const reply = document.querySelector("#reply");
const frameworkLabels = { langgraph: "LangGraph", agents: "OpenAI Agents" };
const sentimentLabels = { positive: "正向 · 鼓励", negative: "负向 · 排解" };

function showError(message) {
  error.textContent = message;
  error.hidden = !message;
}

async function loadFrameworks() {
  try {
    // 本地版可选两个框架，Docker 版仅显示后端启用的 Agents。
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error();
    const { frameworks } = await response.json();
    framework.replaceChildren(...frameworks.map(
      name => new Option(frameworkLabels[name], name),
    ));
    framework.disabled = false;
    submit.disabled = false;
  } catch {
    framework.options[0].text = "服务不可用";
    showError("无法连接后端，请确认服务已启动后刷新页面。");
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  showError("");
  if (!text.value.trim()) {
    showError("请输入想分享的内容。");
    text.focus();
    return;
  }

  // 等待本次请求期间锁定提交按钮和框架选项，并收起上次回应。
  submit.disabled = true;
  submit.textContent = "正在处理…";
  framework.disabled = true;
  status.hidden = false;
  status.textContent = "正在识别情绪并生成回应…";
  sentiment.hidden = true;
  reply.hidden = true;

  try {
    const response = await fetch("/api/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ framework: framework.value, text: text.value.trim() }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(typeof result.detail === "string" ? result.detail : "请检查输入后重试。");
    }
    sentiment.textContent = sentimentLabels[result.sentiment];
    sentiment.dataset.tone = result.sentiment;
    // 把模型回复当作纯文本展示，避免浏览器将其解析成 HTML。
    reply.textContent = result.reply;
    status.hidden = true;
    sentiment.hidden = false;
    reply.hidden = false;
  } catch (failure) {
    status.textContent = "这次没有生成回应。";
    showError(failure instanceof TypeError ? "无法连接后端，请稍后重试。" : failure.message);
  } finally {
    submit.disabled = false;
    submit.textContent = "识别并回应";
    framework.disabled = false;
  }
});

loadFrameworks();
