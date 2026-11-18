const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-text");
const sendButton = document.querySelector("#send-button");
const reply = document.querySelector("#chat-reply");
const chatUrl = "http://127.0.0.1:8000/chat";

function showReply(message, state = "") {
    reply.textContent = message;
    reply.dataset.state = state;
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (sendButton.disabled) return;

    const text = input.value.trim();
    if (!text) {
        showReply("请先输入内容。", "error");
        input.focus();
        return;
    }

    sendButton.disabled = true;
    showReply("正在生成回复…");

    try {
        const url = new URL(chatUrl);
        url.searchParams.set("text", text);
        const response = await fetch(url, { method: "POST" });
        const data = await response.json().catch(() => null);

        if (!response.ok) {
            const message = typeof data?.detail === "string"
                ? data.detail
                : `请求失败（HTTP ${response.status}），请稍后重试。`;
            showReply(message, "error");
        } else if (typeof data === "string" && data.trim()) {
            showReply(data);
        } else {
            showReply("后端没有返回有效回复，请重试。", "error");
        }
    } catch {
        showReply("无法连接后端，请确认服务已启动。", "error");
    } finally {
        sendButton.disabled = false;
    }
});
