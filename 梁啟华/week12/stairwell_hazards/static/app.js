const LEVEL_DESC = {
  high: "存在可能起火的元素，请立即处置并上报",
  medium: "堆积物已阻塞通道或可能助燃，需尽快清理",
  low: "存在堆物现象但不算严重，建议提醒住户清理",
  none: "通道干净，未发现明显堆放物品",
  other: "画面不属于楼道场景，无法评估风险",
};

const state = {
  file: null,
  previewUrl: "",
  busy: false,
  config: null,
  timer: null,
  startedAt: 0,
  history: [],
};

const el = {
  status: document.getElementById("status"),
  statusText: document.getElementById("status-text"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  dropEmpty: document.getElementById("drop-empty"),
  preview: document.getElementById("preview"),
  previewImg: document.getElementById("preview-img"),
  previewBadge: document.getElementById("preview-badge"),
  fileMeta: document.getElementById("file-meta"),
  uploadHint: document.getElementById("upload-hint"),
  resetBtn: document.getElementById("reset-btn"),
  modelSelect: document.getElementById("model-select"),
  analyzeBtn: document.getElementById("analyze-btn"),
  analyzeText: document.getElementById("analyze-text"),
  configNotice: document.getElementById("config-notice"),
  emptyState: document.getElementById("empty-state"),
  loading: document.getElementById("loading"),
  loadingTime: document.getElementById("loading-time"),
  errorBox: document.getElementById("error-box"),
  errorText: document.getElementById("error-text"),
  result: document.getElementById("result"),
  resultMeta: document.getElementById("result-meta"),
  hero: document.getElementById("result-hero"),
  riskBadge: document.getElementById("risk-badge"),
  riskDesc: document.getElementById("risk-desc"),
  scoreValue: document.getElementById("score-value"),
  meterFill: document.getElementById("meter-fill"),
  hazardChips: document.getElementById("hazard-chips"),
  evidenceBlock: document.getElementById("evidence-block"),
  evidenceList: document.getElementById("evidence-list"),
  adviceBlock: document.getElementById("advice-block"),
  adviceText: document.getElementById("advice-text"),
  rawText: document.getElementById("raw-text"),
  historySection: document.getElementById("history-section"),
  historyList: document.getElementById("history-list"),
};

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(date) {
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function setStatus(stateName, text) {
  el.status.dataset.state = stateName;
  el.statusText.textContent = text;
}

function setBusy(busy) {
  state.busy = busy;
  el.analyzeBtn.classList.toggle("is-busy", busy);
  el.analyzeText.textContent = busy ? "识别中…" : "开始识别";
  el.analyzeBtn.disabled = busy || !state.file;
  el.fileInput.disabled = busy;
  el.modelSelect.disabled = busy;
  el.resetBtn.disabled = busy;
  el.dropzone.style.cursor = busy ? "default" : "pointer";
}

function showOnly(view) {
  el.emptyState.hidden = view !== "empty";
  el.loading.hidden = view !== "loading";
  el.errorBox.hidden = view !== "error";
  el.result.hidden = view !== "result";
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const config = await response.json();
    state.config = config;

    el.modelSelect.innerHTML = "";
    for (const model of config.models) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.label;
      option.selected = model.id === config.default_model;
      el.modelSelect.appendChild(option);
    }
    el.uploadHint.textContent = `支持 JPG / PNG / WEBP，单张 ≤ ${config.max_upload_mb}MB`;

    if (config.configured) {
      setStatus("ok", `服务已就绪 · ${config.base_host}`);
      el.configNotice.hidden = true;
    } else {
      setStatus("warn", "未配置 API Key");
      el.configNotice.hidden = false;
      el.configNotice.textContent =
        "尚未配置模型网关：请把 stairwell_hazards/.env.example 复制为 .env，填写 OPENAI_BASE_URL 与 OPENAI_API_KEY 后重启服务。";
    }
  } catch (error) {
    setStatus("error", "服务连接失败");
    el.configNotice.hidden = false;
    el.configNotice.textContent = "无法读取服务配置，请确认后端已启动后刷新页面。";
  }
}

function validateFile(file) {
  const allowed = ["image/jpeg", "image/png", "image/webp", "image/bmp"];
  if (!allowed.includes(file.type)) return "仅支持 JPG / PNG / WEBP / BMP 格式的图片。";
  const limitMb = state.config?.max_upload_mb || 12;
  if (file.size > limitMb * 1024 * 1024) return `图片超过 ${limitMb}MB，请压缩后再上传。`;
  return "";
}

function selectFile(file) {
  const message = validateFile(file);
  if (message) {
    showError(message);
    return;
  }

  state.file = file;
  state.previewUrl = URL.createObjectURL(file);
  el.previewImg.src = state.previewUrl;
  el.previewImg.alt = file.name;
  el.preview.hidden = false;
  el.dropEmpty.hidden = true;
  el.dropzone.classList.add("has-file");
  el.previewBadge.textContent = "待识别";
  el.previewBadge.dataset.level = "";
  el.fileMeta.textContent = `${file.name} · ${formatSize(file.size)}`;
  el.resetBtn.hidden = false;
  el.errorBox.hidden = true;
  showOnly("empty");
  el.resultMeta.textContent = "";
  setBusy(false);
}

function resetAll() {
  state.file = null;
  el.fileInput.value = "";
  el.preview.hidden = true;
  el.previewImg.removeAttribute("src");
  el.dropEmpty.hidden = false;
  el.dropzone.classList.remove("has-file");
  el.fileMeta.textContent = "尚未选择照片";
  el.resetBtn.hidden = true;
  el.resultMeta.textContent = "";
  el.analyzeBtn.disabled = true;
  showOnly("empty");
}

function showError(message) {
  el.errorText.textContent = message;
  showOnly("error");
}

function startTimer() {
  state.startedAt = Date.now();
  el.loadingTime.textContent = "已用时 0.0s";
  state.timer = setInterval(() => {
    const seconds = (Date.now() - state.startedAt) / 1000;
    el.loadingTime.textContent = `已用时 ${seconds.toFixed(1)}s`;
  }, 100);
}

function stopTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
}

function renderHazards(hazards, levelCode) {
  el.hazardChips.innerHTML = "";
  if (!hazards || hazards.length === 0) {
    const empty = document.createElement("span");
    empty.className = "chip-empty";
    empty.textContent = levelCode === "other" ? "非楼道场景，未做隐患判断" : "未发现明显隐患";
    el.hazardChips.appendChild(empty);
    return;
  }

  for (const hazard of hazards) {
    const chip = document.createElement("span");
    chip.className = `chip ${levelCode}`;
    const name = document.createElement("span");
    name.textContent = hazard.type || "隐患";
    chip.appendChild(name);

    if (hazard.location) {
      const location = document.createElement("span");
      location.className = "chip-loc";
      location.textContent = hazard.location;
      chip.appendChild(location);
    }
    if (hazard.detail) chip.title = hazard.detail;
    el.hazardChips.appendChild(chip);
  }
}

function renderResult(data, options = {}) {
  const code = data.risk_code || "other";
  const level = data.risk_level || "待确认";
  const score = Number.isFinite(data.score) ? data.score : 0;

  el.result.dataset.level = code;
  el.hero.dataset.level = code;
  el.riskBadge.dataset.level = code;
  el.riskBadge.textContent = level;
  el.riskDesc.textContent = LEVEL_DESC[code] || "已生成风险评估结果";
  el.scoreValue.textContent = score;

  el.meterFill.style.width = "0%";
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      el.meterFill.style.width = `${Math.min(Math.max(score, 0), 100)}%`;
    });
  });

  renderHazards(data.hazards, code);

  el.evidenceList.innerHTML = "";
  if (data.evidence && data.evidence.length) {
    for (const item of data.evidence) {
      const li = document.createElement("li");
      li.textContent = item;
      el.evidenceList.appendChild(li);
    }
    el.evidenceBlock.hidden = false;
  } else {
    el.evidenceBlock.hidden = true;
  }

  if (data.advice) {
    el.adviceText.textContent = data.advice;
    el.adviceBlock.hidden = false;
  } else {
    el.adviceBlock.hidden = true;
  }

  el.rawText.textContent = data.raw || "（无原始输出）";

  const confidence = Number.isFinite(data.confidence) ? Math.round(data.confidence * 100) : null;
  const metaParts = [];
  if (data.model_label || data.model) metaParts.push(data.model_label || data.model);
  if (Number.isFinite(data.elapsed_ms)) metaParts.push(`耗时 ${(data.elapsed_ms / 1000).toFixed(1)}s`);
  if (confidence !== null) metaParts.push(`置信度 ${confidence}%`);
  if (options.fromHistory) metaParts.push("历史记录");
  el.resultMeta.textContent = metaParts.join(" · ");

  el.previewBadge.textContent = level;
  el.previewBadge.dataset.level = code;
  showOnly("result");
}

function addHistory(data) {
  const item = {
    id: `${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
    url: state.previewUrl,
    file: state.file,
    level: data.risk_level,
    code: data.risk_code,
    time: formatTime(new Date()),
    result: data,
  };
  state.history.unshift(item);
  state.history = state.history.slice(0, 8);
  renderHistory();
}

function renderHistory() {
  el.historyList.innerHTML = "";
  el.historySection.hidden = state.history.length === 0;

  for (const item of state.history) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";

    const img = document.createElement("img");
    img.src = item.url;
    img.alt = item.level;

    const info = document.createElement("span");
    info.className = "history-info";
    const level = document.createElement("span");
    level.className = "history-level";
    level.dataset.level = item.code;
    level.textContent = item.level;
    const time = document.createElement("span");
    time.className = "history-time";
    time.textContent = item.time;
    info.append(level, time);

    button.append(img, info);
    button.addEventListener("click", () => restoreHistory(item));
    el.historyList.appendChild(button);
  }
}

function restoreHistory(item) {
  if (state.busy || !item.file) return;
  state.file = item.file;
  state.previewUrl = item.url;
  el.previewImg.src = item.url;
  el.preview.hidden = false;
  el.dropEmpty.hidden = true;
  el.dropzone.classList.add("has-file");
  el.fileMeta.textContent = `${item.file.name} · ${formatSize(item.file.size)} · ${item.time}`;
  el.resetBtn.hidden = false;
  el.analyzeBtn.disabled = false;
  el.configNotice.hidden = !!(state.config && state.config.configured);

  const model = state.config?.models.find((m) => m.id === item.result.model);
  renderResult({ ...item.result, model_label: model?.label }, { fromHistory: true });
}

async function runAnalysis() {
  if (state.busy || !state.file) return;

  setBusy(true);
  showOnly("loading");
  el.resultMeta.textContent = "";
  startTimer();

  const form = new FormData();
  form.append("file", state.file);
  form.append("model", el.modelSelect.value);

  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data?.detail?.message || `识别请求失败（HTTP ${response.status}）`);
    }
    const modelLabel = state.config?.models.find((m) => m.id === data.model)?.label;
    renderResult({ ...data, model_label: modelLabel });
    addHistory(data);
  } catch (error) {
    const message = error instanceof TypeError
      ? "无法连接后端服务，请确认服务是否正在运行。"
      : error.message;
    showError(message);
  } finally {
    stopTimer();
    setBusy(false);
  }
}

/* ---------- 事件绑定 ---------- */
el.dropzone.addEventListener("click", () => {
  if (!state.busy) el.fileInput.click();
});

el.dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (!state.busy) el.fileInput.click();
  }
});

el.fileInput.addEventListener("change", () => {
  const file = el.fileInput.files?.[0];
  if (file) selectFile(file);
});

for (const type of ["dragenter", "dragover"]) {
  el.dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    if (!state.busy) el.dropzone.classList.add("is-dragging");
  });
}

el.dropzone.addEventListener("dragleave", (event) => {
  if (!el.dropzone.contains(event.relatedTarget)) {
    el.dropzone.classList.remove("is-dragging");
  }
});

el.dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  el.dropzone.classList.remove("is-dragging");
  if (state.busy) return;
  const file = event.dataTransfer?.files?.[0];
  if (file) selectFile(file);
});

document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => event.preventDefault());

window.addEventListener("paste", (event) => {
  if (state.busy) return;
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find((item) => item.type.startsWith("image/"));
  if (!imageItem) return;
  const file = imageItem.getAsFile();
  if (file) selectFile(file);
});

el.resetBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  if (!state.busy) resetAll();
});

el.analyzeBtn.addEventListener("click", runAnalysis);

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    runAnalysis();
  }
});

loadConfig();
