// ============================================
// 多模态 RAG 问答系统 - 前端应用逻辑
// ============================================

const { createApp, ref, reactive, onMounted, nextTick } = Vue;

const App = {
    setup() {
        const API_BASE = '';

        const activeTab = ref('upload');
        const backendStatus = ref('检测中...');

        const uploadFileList = ref([]);
        const uploading = ref(false);
        const docList = ref([]);
        const loadingDocs = ref(false);
        const stats = reactive({ pending: 0, parsing: 0, completed: 0, error: 0 });

        const chatInput = ref('');
        const chatMessages = ref([]);
        const chatImages = ref([]);
        const chatLoading = ref(false);
        const chatUseRAG = ref(true);
        const chatContainer = ref(null);

        const previewVisible = ref(false);
        const previewContent = ref('');

        const quickPrompts = [
            '总结一下文档的主要内容',
            '文档里有哪些关键数据？',
            '帮我列出所有标题',
        ];

        async function apiFetch(url, options = {}) {
            const res = await fetch(`${API_BASE}${url}`, {
                headers: { 'Content-Type': 'application/json', ...options.headers },
                ...options,
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
            return res.json();
        }

        async function checkBackend() {
            try {
                const data = await apiFetch('/api/chat/health');
                backendStatus.value = data.status === 'ok'
                    ? `在线 (向量库 ${data.vector_store_chunks} chunks)`
                    : '部分可用';
            } catch (e) {
                backendStatus.value = '离线';
            }
        }

        function onFileChange(file, list) {
            if (file && file.raw && file.raw.type !== 'application/pdf') {
                ElementPlus.ElMessage.warning('仅支持 PDF 文件');
            }
        }
        function onFileRemove(file, list) { }
        function clearUploadList() { uploadFileList.value = []; }

        async function doUpload() {
            if (uploadFileList.value.length === 0) return;
            uploading.value = true;
            try {
                const formData = new FormData();
                uploadFileList.value.forEach(f => formData.append('files', f.raw || f));
                const res = await fetch('/api/upload/pdfs', {
                    method: 'POST',
                    body: formData,
                });
                const data = await res.json();
                if (data.success) {
                    ElementPlus.ElMessage.success(data.message);
                    uploadFileList.value = [];
                    await refreshDocList();
                } else {
                    ElementPlus.ElMessage.error(data.detail || '上传失败');
                }
            } catch (e) {
                ElementPlus.ElMessage.error(`上传失败: ${e.message}`);
            } finally {
                uploading.value = false;
            }
        }

        async function refreshDocList() {
            loadingDocs.value = true;
            try {
                const data = await apiFetch('/api/documents');
                docList.value = data.documents || [];
                const statsData = await apiFetch('/api/documents/stats');
                Object.assign(stats, statsData);
            } catch (e) {
                ElementPlus.ElMessage.error(`加载文档列表失败: ${e.message}`);
            } finally {
                loadingDocs.value = false;
            }
        }

        let pollTimer = null;
        function startPolling() {
            pollTimer = setInterval(async () => {
                const hasActive = docList.value.some(d =>
                    d.status === 'parsing' || d.status === 'pending'
                );
                if (hasActive) await refreshDocList();
            }, 3000);
        }

        function formatSize(bytes) {
            if (!bytes) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB'];
            let i = 0, size = bytes;
            while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
            return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
        }

        function statusLabel(s) {
            return { pending: '待解析', parsing: '解析中', completed: '已完成', error: '失败' }[s] || s;
        }
        function statusTagType(s) {
            return { pending: 'info', parsing: 'warning', completed: 'success', error: 'danger' }[s] || '';
        }

        function showDocPreview(row) {
            previewContent.value = row.parsed_text_preview || '(暂无预览内容)';
            previewVisible.value = true;
        }

        function renderMarkdown(text) {
            if (!text) return '';
            marked.setOptions({
                breaks: true,
                gfm: true,
                highlight: function (code, lang) {
                    try {
                        if (lang && hljs.getLanguage(lang)) {
                            return hljs.highlight(code, { language: lang }).value;
                        }
                        return hljs.highlightAuto(code).value;
                    } catch (e) { return code; }
                },
            });
            let html = marked.parse(text);
            html = html.replace(/src="(?!https?:\/\/|data:)([^"]+)"/g, (m, p) => `src="/static/parsed/${p}"`);
            return html;
        }

        function sendQuickPrompt(prompt) { chatInput.value = prompt; sendChat(); }

        function handleChatImage(file) {
            const reader = new FileReader();
            reader.onload = (e) => chatImages.value.push(e.target.result);
            reader.readAsDataURL(file.raw);
            return false;
        }

        function removeChatImage(idx) { chatImages.value.splice(idx, 1); }

        function scrollChatToBottom() {
            nextTick(() => {
                if (chatContainer.value) {
                    chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
                }
            });
        }

        async function sendChat() {
            const text = chatInput.value.trim();
            if (!text && chatImages.value.length === 0) return;

            const userMsg = { role: 'user', content: text || '[图片]', images: chatImages.value.slice() };
            chatMessages.value.push(userMsg);
            chatInput.value = '';
            chatImages.value = [];
            chatLoading.value = true;
            scrollChatToBottom();

            try {
                const fd = new FormData();
                fd.append('question', userMsg.content);
                fd.append('use_rag', chatUseRAG.value ? 'true' : 'false');
                userMsg.images.forEach(img => {
                    const arr = img.split(',');
                    const mime = arr[0].match(/:(.*?);/)[1];
                    const bstr = atob(arr[1]);
                    let n = bstr.length;
                    const u8arr = new Uint8Array(n);
                    while (n--) u8arr[n] = bstr.charCodeAt(n);
                    fd.append('images', new Blob([u8arr], { type: mime }), 'image.png');
                });

                const res = await fetch('/api/chat/ask-form-data', { method: 'POST', body: fd });
                const data = await res.json();

                chatMessages.value.push({
                    role: 'assistant',
                    content: data.answer || '(无回答)',
                    sources: data.sources || null,
                });
            } catch (e) {
                chatMessages.value.push({ role: 'assistant', content: `❌ 请求失败: ${e.message}` });
            } finally {
                chatLoading.value = false;
                scrollChatToBottom();
            }
        }

        onMounted(() => {
            checkBackend();
            refreshDocList();
            startPolling();
            setInterval(checkBackend, 15000);
        });

        return {
            activeTab, backendStatus, uploadFileList, uploading, docList,
            loadingDocs, stats, chatInput, chatMessages, chatImages, chatLoading,
            chatUseRAG, chatContainer, previewVisible, previewContent, quickPrompts,
            onFileChange, onFileRemove, clearUploadList, doUpload, refreshDocList,
            formatSize, statusLabel, statusTagType, showDocPreview, renderMarkdown,
            sendQuickPrompt, handleChatImage, removeChatImage, sendChat,
        };
    },
};

const app = createApp(App);
app.use(ElementPlus);
app.mount('#app');
