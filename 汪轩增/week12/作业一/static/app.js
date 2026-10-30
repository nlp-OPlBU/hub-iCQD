(function () {
  "use strict";

  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");
  const preview = document.getElementById("preview");
  const placeholder = document.getElementById("placeholder");
  const resultBox = document.getElementById("result");

  const btnOpen = document.getElementById("btn-open");
  const btnCapture = document.getElementById("btn-capture");
  const btnReset = document.getElementById("btn-reset");
  const btnAnalyze = document.getElementById("btn-analyze");
  const fileInput = document.getElementById("file-input");

  let stream = null;
  // 当前待识别的图片：{ dataUrl, mime }，通过拍照或上传得到
  let currentImage = null;

  // 风险等级对应的文字说明（用于没有 reason 时兜底）
  const RISK_DESC = {
    高风险: "检测到电动车、电瓶、飞线充电等可能起火元素",
    中风险: "存在大量堆积物、纸箱或木质家具等堵塞物",
    低风险: "存在楼道堆物现象但不严重",
    无风险: "楼道干净，无堆放物品",
    非楼道: "图片内容与楼道无关",
  };

  function setPlaceholderVisible(visible) {
    placeholder.classList.toggle("hidden", !visible);
  }

  function showPreview(dataUrl) {
    preview.src = dataUrl;
    preview.classList.remove("hidden");
    video.classList.add("hidden");
    setPlaceholderVisible(false);
  }

  function resetPreview() {
    preview.classList.add("hidden");
    preview.removeAttribute("src");
    setPlaceholderVisible(true);
    if (stream) {
      video.classList.remove("hidden");
    } else {
      video.classList.add("hidden");
    }
  }

  function updateButtons() {
    btnCapture.disabled = !stream;
    btnReset.disabled = !currentImage;
    btnAnalyze.disabled = !currentImage;
  }

  async function openCamera() {
    try {
      if (stream) {
        stopCamera();
      }
      // 打开摄像头意味着重新取景，先清掉上一张待识别图与预览
      currentImage = null;
      preview.classList.add("hidden");
      preview.removeAttribute("src");
      // 后置摄像头优先（facingMode: environment），手机默认拍楼道
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1280 } },
        audio: false,
      });
      video.srcObject = stream;
      video.classList.remove("hidden");
      setPlaceholderVisible(false);
      updateButtons();
    } catch (err) {
      alert(
        "无法打开摄像头：" +
          (err && err.message ? err.message : err) +
          "\n\n提示：手机浏览器调用摄像头需要 HTTPS 安全上下文，" +
          "可改用下方「上传图片」功能。"
      );
    }
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
      video.srcObject = null;
      video.classList.add("hidden");
    }
  }

  function capture() {
    if (!stream) return;
    const vw = video.videoWidth || 640;
    const vh = video.videoHeight || 480;
    canvas.width = vw;
    canvas.height = vh;
    canvas.getContext("2d").drawImage(video, 0, 0, vw, vh);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    currentImage = { dataUrl, mime: "image/jpeg" };
    stopCamera();
    showPreview(dataUrl);
    updateButtons();
  }

  function handleFile(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      alert("请选择图片文件");
      return;
    }
    const reader = new FileReader();
    reader.onload = function () {
      const dataUrl = reader.result;
      currentImage = { dataUrl, mime: file.type };
      stopCamera();
      showPreview(dataUrl);
      updateButtons();
    };
    reader.readAsDataURL(file);
  }

  function reset() {
    currentImage = null;
    stopCamera();
    resetPreview();
    resultBox.classList.add("hidden");
    resultBox.innerHTML = "";
    updateButtons();
  }

  function renderResult(data) {
    resultBox.classList.remove("hidden");
    const level = data.risk_level || "非楼道";
    const reason = data.reason || RISK_DESC[level] || "";

    resultBox.className = "result";
    resultBox.innerHTML =
      '<div class="badge risk-' + level + '">' + level + "</div>" +
      '<div class="reason">' + escapeHtml(reason) + "</div>" +
      '<div class="meta">识别引擎：' +
      escapeHtml(data.provider || "-") +
      " · " +
      escapeHtml(data.model || "-") +
      "</div>";
  }

  function renderError(msg) {
    resultBox.classList.remove("hidden");
    resultBox.className = "result error";
    resultBox.innerHTML = '<div class="reason">识别失败：' + escapeHtml(msg) + "</div>";
  }

  async function analyze() {
    if (!currentImage) return;
    btnAnalyze.disabled = true;
    btnAnalyze.textContent = "识别中…";
    resultBox.classList.add("hidden");
    try {
      const resp = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: currentImage.dataUrl }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "HTTP " + resp.status);
      }
      renderResult(data);
    } catch (err) {
      renderError(err && err.message ? err.message : String(err));
    } finally {
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "开始识别";
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  btnOpen.addEventListener("click", openCamera);
  btnCapture.addEventListener("click", capture);
  btnReset.addEventListener("click", reset);
  btnAnalyze.addEventListener("click", analyze);
  fileInput.addEventListener("change", function () {
    handleFile(this.files && this.files[0]);
    this.value = "";
  });

  updateButtons();
})();
