<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as chat from '../api/chat'
import { ApiError, getCapabilities } from '../api/http'
import ChatInput from '../components/ChatInput.vue'
import ChatMessage from '../components/ChatMessage.vue'
import { usePagePolling } from '../composables/usePagePolling'
import type { Conversation, Message, Question } from '../types/chat'
import type { Capabilities } from '../types/common'
import type { DocumentRecord } from '../types/document'

const route = useRoute()
const conversations = ref<Conversation[]>([]), conversationId = ref('')
const documents = ref<DocumentRecord[]>([]), documentIds = ref<string[]>([])
const allDocuments = ref(true), messages = ref<Message[]>([]), text = ref('')
const files = ref<File[]>([]), busy = ref(true), error = ref(''), documentError = ref(''), historyError = ref('')
const vision = ref<boolean | null>(null), limits = ref<Capabilities | null>(null), capabilityError = ref('')
const documentsLoading = ref(false), imageUploadBlocked = ref(false)
const pending = ref<{ question: Question; key: string; conversationId: string } | null>(null)
let uploaded: { files: File[]; conversationId: string; ids: string[] } | null = null
let messageGeneration = 0, messagesLoading = false
const scopeError = computed(() => {
  if (allDocuments.value) return ''
  if (!documentIds.value.length) return '请至少选择一份可检索文档。'
  if (documentIds.value.length > 100) return '指定范围最多选择 100 份文档。'
  if (documentIds.value.some(id => !documents.value.some(item => item.id === id))) return '指定文档暂不可检索，请刷新文档范围或重新选择；不会自动扩大到全部文档。'
  return ''
})
const signal = usePagePolling(async () => {
  if (!messagesLoading && messages.value.some(message => message.status === 'pending')) await loadMessages()
})
function showError(value: unknown) { if (!signal.aborted) error.value = value instanceof Error ? value.message : '请求失败' }
watch(files, () => { uploaded = null; imageUploadBlocked.value = false })
watch(() => route.query.document, value => {
  const id = Array.isArray(value) ? value[0] : value
  if (id) { allDocuments.value = false; documentIds.value = [id] }
}, { immediate: true })

async function loadMessages() {
  if (!conversationId.value || signal.aborted) return
  const generation = ++messageGeneration, id = conversationId.value
  messagesLoading = true
  try {
    const result: Message[] = []
    let page = 1
    while (!signal.aborted && generation === messageGeneration) {
      const response = await chat.listMessages(id, page++, signal)
      result.push(...response.items)
      if (!response.items.length || result.length >= response.total) break
    }
    if (!signal.aborted && generation === messageGeneration && conversationId.value === id) {
      messages.value = result; historyError.value = ''
    }
  } catch (value) {
    if (!signal.aborted && generation === messageGeneration) historyError.value = value instanceof Error ? value.message : '消息查询失败'
  } finally { if (generation === messageGeneration) messagesLoading = false }
}
function selectConversation() {
  ++messageGeneration; messages.value = []; historyError.value = ''; uploaded = null
  void loadMessages()
}
async function loadConversations() {
  const result: Conversation[] = []
  let page = 1
  while (!signal.aborted) {
    const response = await chat.listConversations(signal, page++)
    result.push(...response.items)
    if (!response.items.length || result.length >= response.total) break
  }
  if (!signal.aborted) conversations.value = result
}
async function loadDocuments() {
  documentsLoading.value = true
  try {
    const result: DocumentRecord[] = []
    let page = 1
    while (!signal.aborted) {
      const response = await chat.listDocuments(page++, signal)
      result.push(...response.items)
      if (!response.items.length || result.length >= response.total) break
    }
    if (!signal.aborted) {
      documents.value = result.filter(item => item.current_searchable_version !== null)
      documentError.value = ''
    }
  } catch (value) { if (!signal.aborted) documentError.value = value instanceof Error ? value.message : '文档范围查询失败' }
  finally { documentsLoading.value = false }
}
async function loadCapabilities() {
  try {
    const response = await getCapabilities(signal)
    if (!signal.aborted) { limits.value = response; vision.value = response.vision_enabled; capabilityError.value = '' }
  } catch { if (!signal.aborted) capabilityError.value = '暂时无法确认视觉配置，图片请求以服务器校验结果为准。' }
}
async function newConversation() {
  const item = await chat.createConversation(signal)
  if (signal.aborted) return
  conversations.value.unshift(item); conversationId.value = item.id
  ++messageGeneration; messages.value = []; historyError.value = ''; uploaded = null
}
async function createNew() {
  busy.value = true; error.value = ''
  try { await newConversation() } catch (value) { showError(value) } finally { busy.value = false }
}
async function submit(replay = false) {
  if (busy.value || signal.aborted || (!replay && (pending.value || scopeError.value || documentError.value || imageUploadBlocked.value))) return
  if (!replay && ((!text.value.trim() && !files.value.length) || (vision.value === false && files.value.length))) return
  busy.value = true; error.value = ''
  try {
    if (!replay) {
      if (!conversationId.value) await newConversation()
      if (signal.aborted) return
      let ids: string[] = []
      if (files.value.length) {
        if (uploaded?.conversationId === conversationId.value && uploaded.files === files.value) ids = uploaded.ids
        else {
          try {
            const response = await chat.uploadImages(conversationId.value, files.value, signal)
            ids = response.items.map(image => image.id)
            uploaded = { files: files.value, conversationId: conversationId.value, ids }
          } catch (value) {
            if (!(value instanceof ApiError) || value.body.error.code === 'IMAGE_UPLOAD_COMMIT_UNCERTAIN') imageUploadBlocked.value = true
            throw value
          }
        }
      }
      pending.value = { conversationId: conversationId.value, key: crypto.randomUUID(), question: {
        text: text.value.trim(), image_ids: ids,
        document_ids: allDocuments.value ? null : [...documentIds.value],
      } }
    }
    const submission = pending.value
    if (!submission || signal.aborted) return
    const answer = await chat.ask(submission.conversationId, submission.question, submission.key, signal)
    if (signal.aborted) return
    pending.value = null; text.value = ''; files.value = []
    messages.value = [...messages.value.filter(message => message.id !== answer.user_message.id && message.id !== answer.assistant_message.id),
      answer.user_message, answer.assistant_message].sort((a, b) => a.sequence - b.sequence)
    if (answer.assistant_message.error) {
      error.value = answer.assistant_message.error.message
      if (answer.assistant_message.error.code === 'VISION_NOT_READY') vision.value = false
    }
    await loadMessages()
  } catch (value) {
    if (value instanceof ApiError) {
      if ((value.status < 500 && value.body.error.code !== 'REQUEST_IN_PROGRESS') || value.body.error.code === 'VISION_NOT_READY') pending.value = null
      if (value.body.error.code === 'VISION_NOT_READY') vision.value = false
      if (value.body.error.code === 'INVALID_DOCUMENT_SCOPE') await loadDocuments()
    }
    showError(value)
    if (!signal.aborted) await loadMessages()
  } finally { busy.value = false }
}
onMounted(async () => {
  // Independent reads: a failed capability or document request must not hide history.
  await Promise.allSettled([
    loadDocuments(), loadCapabilities(),
    (async () => {
      try {
        await loadConversations()
        if (!signal.aborted && conversations.value[0]) { conversationId.value = conversations.value[0].id; await loadMessages() }
      } catch (value) { showError(value) }
    })(),
  ])
  if (!signal.aborted) busy.value = false
})
</script>

<template>
  <section class="panel chat-panel">
    <p class="eyebrow">ASK YOUR DOCUMENTS</p>
    <h2>文档图文问答</h2>
    <p class="muted">选择文档范围，输入问题或图片。点击回答中的 [S1] 等标记查看实际文档来源。</p>
    <div class="notice" role="status">
      <span v-if="vision === false">视觉能力未就绪：当前服务未启用图片问答。仍可进行文字提问，请清除已选图片后发送。</span>
      <span v-else-if="vision === true">服务已启用图片问答；图片处理或模型暂不可用时会显示具体原因。</span>
      <span v-else>{{ capabilityError || '正在读取视觉能力配置…' }}</span>
      <button type="button" class="text-button" :disabled="busy" @click="loadCapabilities">刷新能力配置</button>
    </div>
    <div class="chat-toolbar">
      <select v-model="conversationId" aria-label="会话" :disabled="busy || !!pending" @change="selectConversation">
        <option value="">选择或新建会话</option>
        <option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }} · {{ item.created_at.slice(0, 16) }}</option>
      </select>
      <button :disabled="busy || !!pending" @click="createNew">新会话</button>
      <button class="secondary" :disabled="busy || !conversationId" @click="loadMessages">刷新消息</button>
    </div>
    <fieldset :disabled="busy || !!pending || documentsLoading">
      <legend>文档范围</legend>
      <label><input v-model="allDocuments" type="checkbox" /> 全部可检索文档</label>
      <div v-if="!allDocuments" class="document-scope">
        <label v-for="item in documents" :key="item.id">
          <input v-model="documentIds" type="checkbox" :value="item.id" /> {{ item.original_filename }}（可用版本 {{ item.current_searchable_version }}）
        </label>
      </div>
      <p v-if="scopeError && !documentsLoading" class="error" role="alert">{{ scopeError }}</p>
      <p v-if="!documents.length && !documentsLoading" class="muted">尚无可检索文档。<RouterLink to="/documents">上传并等待解析完成 →</RouterLink></p>
      <p v-if="documentError" class="error" role="alert">{{ documentError }}；请刷新范围后提问。</p>
      <button v-if="!allDocuments && documentIds.length" type="button" class="text-button" @click="documentIds = []">清空选择</button>
      <button type="button" class="text-button" @click="loadDocuments">{{ documentsLoading ? '刷新中…' : '刷新文档范围' }}</button>
    </fieldset>
    <p v-if="historyError" class="error" role="alert">{{ historyError }}；可刷新消息重试。</p>
    <div aria-live="polite" aria-label="会话消息">
      <div v-if="!messages.length" class="empty-state">{{ busy ? '正在加载…' : '输入一个问题开始。每次回答都会列出可核对的文档、页码和相关图片。' }}</div>
      <ChatMessage v-for="message in messages" :key="message.id" :message="message" />
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="imageUploadBlocked" class="error" role="alert">图片上传结果无法确认，已停止重复上传。请核对后再操作；可清除所选图片继续文字提问。</p>
    <ChatInput v-model:text="text" v-model:files="files" :busy="busy" :locked="!!pending" :blocked="!!scopeError || !!documentError || imageUploadBlocked || documentsLoading" :vision="vision" :limits="limits" @submit="submit()" @replay="submit(true)" />
  </section>
</template>
