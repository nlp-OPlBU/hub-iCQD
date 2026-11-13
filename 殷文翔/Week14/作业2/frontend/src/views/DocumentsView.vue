<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import * as api from '../api/documents'
import { ApiError, getCapabilities } from '../api/http'
import DocumentList from '../components/DocumentList.vue'
import PdfUploader from '../components/PdfUploader.vue'
import { usePagePolling } from '../composables/usePagePolling'
import type { Capabilities } from '../types/common'
import type { BatchUploadResponse, DocumentRecord, TaskStatus } from '../types/document'

const documents = ref<DocumentRecord[]>([]), total = ref(0), page = ref(1)
const status = ref<TaskStatus | ''>(''), loading = ref(false), uploading = ref(false)
const files = ref<File[]>([]), result = ref<BatchUploadResponse | null>(null)
const limits = ref<Capabilities | null>(null), error = ref(''), uploadError = ref('')
const retrying = ref<string[]>([]), retryErrors = ref<Record<string, string>>({})
const retries = ref<Record<string, { version: number; key: string }>>({})
const unresolved = computed(() => Object.keys(retries.value))
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
let generation = 0
const signal = usePagePolling(async () => { if (!loading.value) await refresh() })

async function refresh() {
  const current = ++generation
  loading.value = true
  try {
    const response = await api.listDocuments(page.value, status.value || undefined, signal)
    if (signal.aborted || current !== generation) return
    documents.value = response.items; total.value = response.total; error.value = ''
    if (page.value > pages.value) { page.value = pages.value; await refresh() }
  } catch (value) {
    if (!signal.aborted && current === generation) error.value = value instanceof Error ? value.message : '文档查询失败'
  } finally { if (current === generation) loading.value = false }
}
function changePage(next: number) { page.value = next; void refresh() }
function changeStatus() { page.value = 1; void refresh() }
async function upload(selected: File[]) {
  if (uploading.value || !selected.length) return
  uploading.value = true; uploadError.value = ''; result.value = null
  try {
    result.value = await api.uploadDocuments(selected, signal)
  } catch (value) {
    if (!signal.aborted) {
      const message = value instanceof Error ? value.message : '上传请求失败'
      uploadError.value = `${message}。若响应丢失或提交结果不明，请先核对下方文档列表，再选择需要补传的文件；系统不会自动重传。`
    }
  } finally {
    files.value = []; uploading.value = false
    if (!signal.aborted) { page.value = 1; status.value = ''; await refresh() }
  }
}
async function retry(item: DocumentRecord) {
  if (retrying.value.includes(item.id)) return
  const submission = retries.value[item.id] || { version: item.latest_version, key: crypto.randomUUID() }
  retries.value[item.id] = submission
  retrying.value.push(item.id); delete retryErrors.value[item.id]
  try {
    const task = await api.retryDocument(item.id, submission.version, submission.key, signal)
    if (!signal.aborted) documents.value = documents.value.map(document => document.id === item.id
      ? { ...document, latest_version: task.version, latest_task: task, updated_at: task.updated_at } : document)
    delete retries.value[item.id]
  } catch (value) {
    if (!signal.aborted) {
      const definitive = value instanceof ApiError && value.status < 500
      if (definitive) delete retries.value[item.id]
      retryErrors.value[item.id] = `${value instanceof Error ? value.message : '重试请求失败'}${definitive ? '；已刷新任务状态。' : '；可使用“获取重试结果”核对同一次请求。'}`
    }
  } finally {
    retrying.value = retrying.value.filter(id => id !== item.id)
    if (!signal.aborted) await refresh()
  }
}
onMounted(async () => {
  try { limits.value = await getCapabilities(signal) } catch { /* Server still validates every upload. */ }
})
</script>

<template>
  <section class="panel">
    <p class="eyebrow">DOCUMENTS</p>
    <h2>上传文档，建立知识库</h2>
    <p class="muted">上传 → 等待解析完成 → 图文提问 → 核对来源。任务状态自动刷新。</p>
    <PdfUploader v-model="files" :busy="uploading" :limits="limits" @upload="upload" />
    <p v-if="uploadError" class="error" role="alert">{{ uploadError }}</p>
    <section v-if="result" class="upload-results" aria-label="逐文件上传结果" aria-live="polite">
      <h3>本批上传结果：{{ result.accepted }} 个已接收，{{ result.rejected }} 个被拒绝</h3>
      <ol>
        <li v-for="entry in result.results" :key="entry.index">
          <strong>{{ entry.index + 1 }}. {{ entry.filename }}</strong>
          <span v-if="entry.outcome === 'accepted'" class="success"> 已接收 · 等待异步解析</span>
          <span v-else class="error"> 被拒绝：{{ entry.error.message }}（{{ entry.error.code }}）</span>
        </li>
      </ol>
      <small class="muted">请求编号：{{ result.request_id }}</small>
    </section>
    <div class="list-toolbar">
      <h3>文档与解析任务 <small>共 {{ total }} 份</small></h3>
      <div class="actions">
        <label>任务状态 <select v-model="status" @change="changeStatus">
          <option value="">全部状态</option><option value="pending">待解析</option><option value="processing">解析中</option><option value="completed">解析完成</option><option value="failed">解析失败</option>
        </select></label>
        <button class="secondary" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新文档' }}</button>
      </div>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}；保留上次列表，稍后自动刷新，也可手动重试。</p>
    <div v-if="!documents.length && !loading && !error" class="empty-state">{{ status ? '当前状态下没有文档，请切换筛选。' : '还没有文档，选择 PDF 开始上传。' }}</div>
    <DocumentList :documents="documents" :retrying="retrying" :unresolved="unresolved" :retry-errors="retryErrors" @retry="retry" />
    <div v-if="total" class="pagination" aria-label="文档分页">
      <button class="secondary" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ pages }} 页</span>
      <button class="secondary" :disabled="page >= pages || loading" @click="changePage(page + 1)">下一页</button>
    </div>
  </section>
</template>
