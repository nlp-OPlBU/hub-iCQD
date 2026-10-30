/* 楼道消防隐患识别 - 前端逻辑
 * 双通道采集：文件选择/系统相机（兼容 http） + getUserMedia 实时取景（需 https/localhost）
 */

const els = {
  video: document.getElementById("video"),
  preview: document.getElementById("preview"),
  placeholder: document.getElementById("placeholder"),
  fileInput: document.getElementById("fileInput"),
  liveBtn: document.getElementById("liveBtn"),
  shotBtn: document.getElementById("shotBtn"),
  hint: document.getElementById("hint"),
  status: document.getElementById("status"),
  statusText: document.getElementById("statusText"),
  result: document.getElementById("result"),
  riskBadge: document.getElementById("riskBadge"),
  riskScene: document.getElementById("riskScene"),
  sceneDesc: document.getElementById("sceneDesc"),
  hazardList: document.getElementById("hazardList"),
  hazardBlock: document.getElementById("hazardBlock"),
  reasonList: document.getElementById("reasonList"),
  reasonBlock: document.getElementById("reasonBlock"),
  suggestionList: document.getElementById("suggestionList"),
  suggestionBlock: document.getElementById("suggestionBlock"),
  uncertainBlock: document.getElementById("uncertainBlock"),
  uncertainList: document.getElementById("uncertainList"),
  meta: document.getElementById("meta"),
  historyWrap: document.getElementById("historyWrap"),
  historyList: document.getElementById("historyList"),
};

const RISK = {
  none: { label: "无风险", color: "#16a34a" },
  low: { label: "低风险", color: "#ca8a04" },
  medium: { label: "中风险", color: "#ea580c" },
  high: { label: "高风险", color: "#dc2626" },
  not_corridor: { label: "非楼道", color: "#64748b" },
};

const MAX_SIDE = 1300;
const historyItems = [];

let stream = null;
let busy = false;
let currentBlobUrl = null;

/* ---------------- 图片处理 ---------------- */

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图片读取失败"));
    };
    img.src = url;
  });
}

/** 上传前压缩：长边 1300px、JPEG 0.85，兼顾清晰度与请求体积 */
async function compressImage(file) {
  const img = await loadImage(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
  const width = Math.round(img.width * scale);
  const height = Math.round(img.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(img, 0, 0, width, height);

  return new Promise((resolve) =>
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.85)
  );
}

function showPreview(blob) {
  if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
  currentBlobUrl = URL.createObjectURL(blob);
  els.preview.src = currentBlobUrl;
  els.preview.classList.add("active");
  els.video.classList.remove("active");
  els.placeholder.hidden = true;
}

function setHint(text, isError = false) {
  els.hint.textContent = text;
  els.hint.classList.toggle("error", isError);
}

/* ---------------- 识别 ---------------- */

async function analyze(blob) {
  if (busy) return;
  busy = true;

  const started = Date.now();
  els.status.hidden = false;
  els.result.hidden = true;
  els.statusText.textContent = "正在识别…";
  const timer = setInterval(() => {
    els.statusText.textContent = `正在识别… 已用 ${((Date.now() - started) / 1000).toFixed(1)}s`;
  }, 200);

  try {
    const form = new FormData();
    form.append("file", blob, "photo.jpg");
    const res = await fetch("/api/analyze", { method: "POST", body: form });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const message = (body.error && body.error.message) || body.detail;
      throw new Error(message || `识别失败（HTTP ${res.status}）`);
    }
    const data = await res.json();
    render(data, currentBlobUrl);
    addHistory(currentBlobUrl, data.risk_level);
    setHint("识别完成，可继续拍摄下一张。");
  } catch (err) {
    setHint(err.message || "识别失败，请重试", true);
    els.result.hidden = true;
  } finally {
    clearInterval(timer);
    els.status.hidden = true;
    busy = false;
  }
}

async function handleFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    setHint("请选择图片文件", true);
    return;
  }
  try {
    const blob = await compressImage(file);
    showPreview(blob);
    await analyze(blob);
  } catch (err) {
    setHint(err.message || "图片处理失败", true);
  }
}

/* ---------------- 结果渲染 ---------------- */

function render(data, thumbUrl) {
  const risk = RISK[data.risk_level] || RISK.low;
  els.riskBadge.textContent = data.risk_label || risk.label;
  els.riskBadge.className = `badge level-${data.risk_level}`;
  els.riskScene.textContent = data.scene.is_corridor ? "场景：楼道" : "场景：非楼道";
  els.sceneDesc.textContent = data.scene.desc || "";

  // 隐患列表
  els.hazardList.innerHTML = "";
  const hazards = (data.hazards || []).filter((h) => h && h.type);
  els.hazardBlock.hidden = hazards.length === 0;
  hazards.forEach((h) => {
    const li = document.createElement("li");
    const metaParts = [];
    if (h.position) metaParts.push(`位置：${h.position}`);
    metaParts.push(`可燃：${h.flammable}`);
    if (h.blocks_passage) metaParts.push("堵塞疏散通道");
    li.innerHTML = `
      <div class="hazard-top">
        <span class="hazard-tag">${escapeHtml(h.type)}</span>
        <span class="sev ${escapeHtml(h.severity)}">${severityLabel(h.severity)}</span>
      </div>
      <p class="hazard-meta">${escapeHtml(metaParts.join(" · "))}</p>
      ${h.ignition_risk ? `<p class="hazard-note">${escapeHtml(h.ignition_risk)}</p>` : ""}
    `;
    els.hazardList.appendChild(li);
  });

  fillList(els.reasonList, els.reasonBlock, data.reasons);
  fillList(els.suggestionList, els.suggestionBlock, data.suggestions);
  fillList(els.uncertainList, els.uncertainBlock, data.uncertain);

  const meta = [
    `模型：${data.model || "deepseek-flash"}`,
    `耗时：${(data.latency_ms / 1000).toFixed(1)}s`,
    data.tokens ? `tokens：${data.tokens}` : "",
    `置信度：${Math.round((data.confidence || 0) * 100)}%`,
    data.corrected ? `规则校正：${data.model_raw_level} → ${data.risk_level}` : "",
    data.cached ? "缓存命中" : "",
  ].filter(Boolean);
  els.meta.innerHTML = meta.map((t) => `<span>${escapeHtml(t)}</span>`).join("");

  els.result.hidden = false;
  els.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function fillList(listEl, blockEl, items) {
  const list = (items || []).filter((x) => x && x.trim());
  blockEl.hidden = list.length === 0;
  listEl.innerHTML = list.map((x) => `<li>${escapeHtml(x)}</li>`).join("");
}

function severityLabel(level) {
  return { high: "高", medium: "中", low: "低" }[level] || "低";
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

/* ---------------- 历史记录 ---------------- */

function addHistory(thumbUrl, level) {
  historyItems.unshift({ thumb: thumbUrl, level, time: new Date() });
  if (historyItems.length > 6) historyItems.pop();

  els.historyWrap.hidden = false;
  els.historyList.innerHTML = "";
  historyItems.forEach((item) => {
    const risk = RISK[item.level] || RISK.low;
    const li = document.createElement("li");
    const time = item.time.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    li.innerHTML = `
      <img src="${item.thumb}" alt="${escapeHtml(risk.label)}" />
      <span class="h-label ${item.level}">${escapeHtml(risk.label)}</span>
      <span>${time}</span>
    `;
    els.historyList.appendChild(li);
  });
}

/* ---------------- 摄像头 ---------------- */

async function startLive() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setHint("当前浏览器不支持实时取景，请用“拍摄 / 选择照片”按钮。", true);
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    els.video.srcObject = stream;
    els.video.classList.add("active");
    els.preview.classList.remove("active");
    els.placeholder.hidden = true;
    els.shotBtn.hidden = false;
    els.liveBtn.hidden = true;
    setHint("实时取景已开启，对准楼道后点“拍下并识别”。");
  } catch (err) {
    stopLive();
    setHint(
      "无法开启摄像头（http 访问或未授权时会失败）。请改用“拍摄 / 选择照片”，手机会直接唤起系统相机。",
      true
    );
  }
}

function stopLive() {
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }
}

async function shoot() {
  if (!els.video.videoWidth) {
    setHint("摄像头尚未就绪，请稍等再试。", true);
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = els.video.videoWidth;
  canvas.height = els.video.videoHeight;
  canvas.getContext("2d").drawImage(els.video, 0, 0);
  const blob = await new Promise((resolve) =>
    canvas.toBlob((b) => resolve(b), "image/jpeg", 0.9)
  );
  stopLive();
  els.shotBtn.hidden = true;
  els.liveBtn.hidden = false;
  showPreview(blob);
  await analyze(blob);
}

/* ---------------- 事件绑定 ---------------- */

els.fileInput.addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  handleFile(file);
  event.target.value = "";
});
els.liveBtn.addEventListener("click", startLive);
els.shotBtn.addEventListener("click", shoot);
window.addEventListener("beforeunload", stopLive);

// 启动时探测环境，给出针对性提示
(async () => {
  const secure = window.isSecureContext || location.hostname === "localhost";
  if (!secure && navigator.mediaDevices) {
    els.liveBtn.title = "当前为 http 访问，实时取景可能不可用";
  }
  try {
    const res = await fetch("/api/health");
    if (res.ok) {
      const info = await res.json();
      if (!info.key_configured) {
        setHint("后端未配置 DEEPSEEK_API_KEY，请检查 .env 文件。", true);
      }
    }
  } catch (err) {
    /* 健康检查失败不影响主流程 */
  }
  if (!secure) {
    setHint("当前是 http 访问：请用“拍摄 / 选择照片”唤起手机相机，这是最稳的方式。");
  }
})();
