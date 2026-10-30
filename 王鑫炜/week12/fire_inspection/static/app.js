"use strict";
const $ = (id) => document.getElementById(id);
let stream = null, photo = null, previewUrl = null, busy = false, cameraOpening = false;
let cameraGeneration = 0, requestTimeout = 65000;
const controls = ["open-camera", "take-photo", "stop-camera", "phone-camera", "upload"];

function error(message) { $("error").textContent = message; $("error").hidden = !message; }
function announce(message) { $("status-announcement").textContent = message; }
function clearResult() { $("result").hidden = true; $("result-empty").hidden = false; }
function setBusy(value) {
  busy = value;
  controls.forEach((id) => { $(id).disabled = value; });
  $("analyze").disabled = value || !photo;
  $("analyze-text").textContent = value ? "正在识别，请稍候…" : "开始风险识别";
  document.querySelector(".result-panel").setAttribute("aria-busy", String(value));
}
function stopCamera() {
  cameraGeneration += 1;
  cameraOpening = false;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  $("video").srcObject = null;
  $("video").hidden = true;
  $("take-photo").hidden = true;
  $("stop-camera").hidden = true;
  $("open-camera").hidden = false;
  $("preview").hidden = !photo;
  $("preview-tag").hidden = !photo;
  $("empty").hidden = Boolean(photo);
}
function cameraMessage(message) { $("camera-message").textContent = message; $("camera-message").hidden = !message; }
function acceptPhoto(file) {
  if (!file || busy) return;
  error("");
  if (file.size > 10 * 1024 * 1024) { error("照片不能超过 10 MB，请压缩后重试。"); return; }
  stopCamera();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  photo = file;
  previewUrl = URL.createObjectURL(file);
  $("preview").src = previewUrl;
  $("preview").hidden = false;
  $("preview-tag").hidden = false;
  $("empty").hidden = true;
  $("file-meta").textContent = `${file.name || "现场照片"} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
  clearResult();
  setBusy(false);
  announce("照片已选好，可以开始风险识别。");
}
$("preview").addEventListener("error", () => {
  if (!photo) return;
  photo = null;
  $("preview").hidden = true;
  $("preview-tag").hidden = true;
  $("empty").hidden = false;
  setBusy(false);
  error("浏览器无法显示该图片。HEIC 请转成 JPEG 后上传。");
});
$("open-camera").addEventListener("click", async () => {
  if (busy || cameraOpening) return;
  error(""); cameraMessage("");
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    cameraMessage("实时预览需要 HTTPS 或本机 localhost。手机可尝试“手机拍照”，或上传已有照片。");
    return;
  }
  cameraOpening = true;
  const generation = ++cameraGeneration;
  $("open-camera").disabled = true;
  try {
    const acquired = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: "environment"}}, audio: false});
    if (generation !== cameraGeneration || busy) { acquired.getTracks().forEach((track) => track.stop()); return; }
    stream = acquired;
    $("video").srcObject = stream;
    await $("video").play();
    if (generation !== cameraGeneration || busy) return;
    $("video").hidden = false;
    $("empty").hidden = true;
    $("preview").hidden = true;
    $("preview-tag").hidden = true;
    $("open-camera").hidden = true;
    $("take-photo").hidden = false;
    $("stop-camera").hidden = false;
  } catch (err) {
    if (generation === cameraGeneration) {
      stopCamera();
      cameraMessage(err.name === "NotAllowedError" ? "摄像头权限被拒绝。请在浏览器中允许访问，或使用手机拍照、上传图片。" : "摄像头暂不可用，请尝试手机拍照或上传图片。");
    }
  } finally { cameraOpening = false; $("open-camera").disabled = busy; }
});
$("stop-camera").addEventListener("click", stopCamera);
$("take-photo").addEventListener("click", () => {
  const video = $("video");
  if (!video.videoWidth) { error("摄像头尚未就绪，请稍后再拍。"); return; }
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
  canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  canvas.toBlob((blob) => {
    if (blob) acceptPhoto(new File([blob], "corridor-photo.jpg", {type: "image/jpeg"}));
    else error("拍照失败，请重试。");
  }, "image/jpeg", 0.9);
});
$("phone-camera").addEventListener("click", () => $("capture-input").click());
$("upload").addEventListener("click", () => $("file-input").click());
["file-input", "capture-input"].forEach((id) => $(id).addEventListener("change", (event) => {
  acceptPhoto(event.target.files[0]); event.target.value = "";
}));
function fillList(id, values) {
  $(id).replaceChildren(...values.map((value) => { const item = document.createElement("li"); item.textContent = value; return item; }));
}
$("analyze").addEventListener("click", async () => {
  if (busy || !photo) return;
  stopCamera(); error(""); clearResult(); setBusy(true); announce("正在识别照片。");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeout);
  try {
    const body = new FormData(); body.append("image", photo);
    const response = await fetch("/api/inspect", {method: "POST", body, signal: controller.signal});
    let data;
    try { data = await response.json(); } catch { throw new Error("服务器返回格式异常，请重试。"); }
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "图片提交失败，请检查图片并重试。");
    $("result-empty").hidden = true; $("result").hidden = false;
    const allowed = ["high", "medium", "low", "none", "non_corridor"];
    $("risk-card").className = "risk-card " + (allowed.includes(data.risk_level) ? data.risk_level : "review");
    $("risk-caption").textContent = data.status === "needs_review" ? "证据不足 / 请人工复核" : "本次识别";
    $("risk-label").textContent = data.risk_label; $("reason").textContent = data.reason;
    fillList("evidence", data.observations.evidence); fillList("actions", data.actions);
    fillList("uncertainties", data.observations.uncertainties);
    $("uncertainties-section").hidden = data.observations.uncertainties.length === 0;
    $("result-meta").textContent = `${data.model} · ${(data.elapsed_ms / 1000).toFixed(1)} 秒`;
    announce(`识别完成：${data.risk_label}。${data.reason}`);
  } catch (err) {
    const message = err.name === "AbortError" ? "识别超时，请稍后重试。本次未生成风险结论。" : (err.message || "网络连接失败，请重试。");
    error(message); announce(message);
  } finally { clearTimeout(timer); setBusy(false); }
});
fetch("/api/config").then((response) => { if (!response.ok) throw new Error(); return response.json(); }).then((config) => {
  requestTimeout = Math.max(10000, (config.request_timeout_seconds || 65) * 1000);
  $("connection").textContent = config.configured ? `识别模型：${config.model} · 照片不在服务器留存` : "尚未配置模型，请先填写后端 .env 后重启服务。";
}).catch(() => { $("connection").textContent = "无法读取服务配置，请确认后端已启动。"; });
window.addEventListener("pagehide", () => { stopCamera(); if (previewUrl) URL.revokeObjectURL(previewUrl); });
document.addEventListener("visibilitychange", () => { if (document.hidden) stopCamera(); });
