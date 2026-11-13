<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as chat from '../api/chat'
import { ApiError } from '../api/http'
import ChatMessage from '../components/ChatMessage.vue'
import type { Conversation, Message, Question } from '../types/chat'
import type { DocumentRecord } from '../types/document'

const conversations = ref<Conversation[]>([]), conversationId = ref('')
const documents = ref<DocumentRecord[]>([]), documentIds = ref<string[]>([])
const allDocuments = ref(true), messages = ref<Message[]>([]), text = ref('')
const files = ref<File[]>([]), previews = ref<string[]>([]), busy = ref(false), error = ref('')
const fileInput = ref<HTMLInputElement>()
const pending = ref<{ question: Question; key: string; conversationId: string } | null>(null)
const controller = new AbortController()
let timer: ReturnType<typeof setInterval> | undefined
let loading = false
function showError(value: unknown) { if (!controller.signal.aborted) error.value = value instanceof Error ? value.message : '请求失败' }
function clearFiles() {
  previews.value.forEach(url => URL.revokeObjectURL(url))
  previews.value = []; files.value = []
  if (fileInput.value) fileInput.value.value = ''
}
function selectFiles(event: Event) {
  const chosen = Array.from((event.target as HTMLInputElement).files || [])
  previews.value.forEach(url => URL.revokeObjectURL(url))
  files.value = chosen
  previews.value = chosen.map(file => URL.createObjectURL(file))
}
async function loadMessages() {
  if (!conversationId.value || loading) return
  loading = true
  const id = conversationId.value
  try {
    const result: Message[] = []
    let page = 1
    while (true) {
      const response = await chat.listMessages(id, page++, controller.signal)
      result.push(...response.items)
      if (result.length >= response.total) break
    }
    if (conversationId.value === id) messages.value = result
  } catch (value) { showError(value) } finally { loading = false }
}
async function newConversation() {
  const item = await chat.createConversation(controller.signal)
  conversations.value.unshift(item); conversationId.value = item.id; messages.value = []
}
async function createNew() {
  busy.value = true; error.value = ''
  try { await newConversation() } catch (value) { showError(value) } finally { busy.value = false }
}
async function submit(replay = false) {
  busy.value = true; error.value = ''
  try {
    if (!replay) {
      if (!allDocuments.value && !documentIds.value.length) throw new Error('请至少选择一份文档')
      if (!conversationId.value) await newConversation()
      const uploaded = files.value.length ? await chat.uploadImages(conversationId.value, files.value, controller.signal) : { items: [] }
      pending.value = { conversationId: conversationId.value, key: crypto.randomUUID(), question: {
        text: text.value, image_ids: uploaded.items.map(image => image.id),
        document_ids: allDocuments.value ? null : [...documentIds.value],
      } }
    }
    const submission = pending.value!
    const answer = await chat.ask(submission.conversationId, submission.question, submission.key, controller.signal)
    pending.value = null; text.value = ''; clearFiles()
    if (answer.assistant_message.error) error.value = answer.assistant_message.error.message
    await loadMessages()
  } catch (value) {
    if (value instanceof ApiError && value.status < 500 && value.body.error.code !== 'REQUEST_IN_PROGRESS') pending.value = null
    showError(value); await loadMessages()
  } finally { busy.value = false }
}
onMounted(async () => {
  busy.value = true
  try {
    const response = await chat.listConversations(controller.signal)
    conversations.value = response.items
    if (response.items[0]) { conversationId.value = response.items[0].id; await loadMessages() }
    let page = 1
    const result: DocumentRecord[] = []
    while (true) {
      const response = await chat.listDocuments(page++, controller.signal)
      result.push(...response.items)
      if (result.length >= response.total) break
    }
    documents.value = result.filter(item => item.current_searchable_version !== null)
  } catch (value) { showError(value) } finally { busy.value = false }
  timer = setInterval(() => {
    if (messages.value.some(message => message.status === 'pending')) void loadMessages()
  }, 3000)
})
onBeforeUnmount(() => { controller.abort(); clearFiles(); if (timer) clearInterval(timer) })
</script>

<template>
  <section class="panel chat-panel">
    <h2>文档图文问答</h2>
    <p>选择文档范围，输入问题或图片。回答中的 [S1] 等标记对应下方实际文档来源。</p>
    <div class="chat-toolbar">
      <select v-model="conversationId" aria-label="会话" :disabled="busy || !!pending" @change="loadMessages">
        <option value="">选择或新建会话</option>
        <option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }} · {{ item.created_at.slice(0, 16) }}</option>
      </select>
      <button :disabled="busy || !!pending" @click="createNew">新会话</button>
      <button :disabled="busy" @click="loadMessages">刷新消息</button>
    </div>
    <fieldset :disabled="busy || !!pending">
      <legend>文档范围</legend>
      <label><input v-model="allDocuments" type="checkbox" /> 全部可检索文档</label>
      <div v-if="!allDocuments" class="document-scope">
        <label v-for="item in documents" :key="item.id">
          <input v-model="documentIds" type="checkbox" :value="item.id" /> {{ item.original_filename }}（可用版本 {{ item.current_searchable_version }}）
        </label>
        <p v-if="!documents.length">尚无可检索文档。</p>
      </div>
    </fieldset>
    <div aria-live="polite">
      <ChatMessage v-for="message in messages" :key="message.id" :message="message" />
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form @submit.prevent="submit()">
      <label for="question">问题（可仅上传图片）</label>
      <textarea id="question" v-model="text" rows="4" maxlength="20000" :disabled="busy || !!pending" placeholder="这张图与所选文档中的流程有什么关联？" />
      <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp" multiple :disabled="busy || !!pending" aria-label="选择问题图片" @change="selectFiles" />
      <div class="image-strip"><img v-for="preview in previews" :key="preview" :src="preview" alt="待发送图片预览" /></div>
      <button v-if="!pending" type="submit" :disabled="busy || (!text.trim() && !files.length)">{{ busy ? '处理中…' : '发送问题' }}</button>
      <button v-else type="button" :disabled="busy" @click="submit(true)">{{ busy ? '处理中…' : '使用原请求获取结果' }}</button>
      <button type="button" :disabled="busy || !!pending" @click="clearFiles">清除图片</button>
    </form>
  </section>
</template>
