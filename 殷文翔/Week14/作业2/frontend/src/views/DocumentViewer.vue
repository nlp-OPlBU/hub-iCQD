<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { documentContentUrl, getDocument, getDocumentPage } from '../api/documents'
import type { DocumentPage, DocumentRecord, EvidenceBlock } from '../types/document'
import { evidenceLabels } from '../utils/documents'
import { isImageResource } from '../utils/resources'

const route = useRoute(), router = useRouter()
const document = ref<DocumentRecord | null>(null), evidence = ref<DocumentPage | null>(null)
const error = ref(''), evidenceError = ref(''), loading = ref(false)
const version = ref(1), pageNumber = ref(1), pageInput = ref(1)
let controller: AbortController | undefined
function integer(value: unknown, min = 1): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= min ? number : null
}
const blockIndex = computed(() => integer(route.query.block, 0))
const contentUrl = computed(() => document.value ? documentContentUrl(document.value.id, version.value) : '')
const pdfUrl = computed(() => `${contentUrl.value}#page=${pageNumber.value}`)
const blocks = computed(() => evidence.value?.blocks.filter(block => blockIndex.value === null || block.block_index === blockIndex.value) || [])
function splitText(block: EvidenceBlock) {
  const start = integer(route.query.offset, 0), end = integer(route.query.end)
  const characters = Array.from(block.text)
  if (blockIndex.value === block.block_index && start !== null && end !== null && start < end && end <= characters.length) {
    return [characters.slice(0, start).join(''), characters.slice(start, end).join(''), characters.slice(end).join('')]
  }
  return [block.text, '', '']
}
function goToPage() {
  const nextPage = Number(pageInput.value)
  if (!Number.isSafeInteger(nextPage) || nextPage < 1 || (evidence.value && nextPage > evidence.value.page_count)) return
  router.replace({ path: route.path, query: { version: version.value, page: nextPage } })
}
watch(() => route.fullPath, async () => {
  controller?.abort()
  const active = new AbortController(); controller = active
  document.value = null; evidence.value = null; error.value = ''; evidenceError.value = ''; loading.value = true
  try {
    const selectedVersion = route.query.version == null ? null : integer(route.query.version)
    const selectedPage = route.query.page == null ? 1 : integer(route.query.page)
    if ((route.query.version != null && selectedVersion === null) || selectedPage === null) throw new Error('文档版本或页码无效')
    const record = await getDocument(String(route.params.id), active.signal)
    if (active.signal.aborted) return
    version.value = selectedVersion ?? record.current_searchable_version ?? record.latest_version
    pageNumber.value = selectedPage; pageInput.value = selectedPage
    document.value = record
    try {
      const result = await getDocumentPage(record.id, version.value, selectedPage, active.signal)
      if (active.signal.aborted) return
      evidence.value = result
      if (blockIndex.value !== null && !blocks.value.length) evidenceError.value = '此版本页面中没有对应原文块'
      await nextTick()
      window.document.querySelector<HTMLElement>('.evidence-block.selected')?.focus({ preventScroll: true })
    } catch (reason) {
      if (!active.signal.aborted) evidenceError.value = reason instanceof Error ? reason.message : '解析证据加载失败'
    }
  } catch (reason) {
    if (!active.signal.aborted) error.value = reason instanceof Error ? reason.message : '文档加载失败'
  } finally {
    if (!active.signal.aborted) loading.value = false
  }
}, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section class="panel document-viewer">
    <RouterLink to="/documents">← 文档管理</RouterLink>
    <h2>{{ document?.original_filename || '文档在线查看' }}</h2>
    <p v-if="loading" role="status">正在加载文档…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="document">
      <form class="viewer-toolbar" @submit.prevent="goToPage">
        <span>解析版本 v{{ version }}</span>
        <label>页码 <input v-model="pageInput" type="number" min="1" :max="evidence?.page_count" required /></label>
        <span v-if="evidence">/ {{ evidence.page_count }} 页</span>
        <button type="submit">跳转</button>
        <a :href="pdfUrl" target="_blank" rel="noopener noreferrer">打开原始 PDF ↗</a>
      </form>
      <p class="muted">原始 PDF 与解析证据并列显示；引用位置在右侧高亮。浏览器未显示 PDF 时，可使用“打开原始 PDF”。</p>
      <div class="viewer-layout">
        <iframe :key="pdfUrl" :src="pdfUrl" :title="`${document.original_filename} 第 ${pageNumber} 页`" class="pdf-frame" />
        <aside class="page-evidence" aria-label="页面解析证据">
          <h3>第 {{ pageNumber }} 页来源</h3>
          <p v-if="evidenceError" class="error" role="alert">{{ evidenceError }}</p>
          <p v-if="blockIndex !== null && evidence"><RouterLink :to="{ path: route.path, query: { version, page: pageNumber } }">查看本页全部证据</RouterLink></p>
          <article v-for="block in blocks" :key="block.block_index" class="evidence-block" :class="{ selected: block.block_index === blockIndex }" tabindex="-1">
            <h4>{{ evidenceLabels[block.evidence_type] }} · 原文块 {{ block.block_index + 1 }}</h4>
            <small v-if="block.bbox">页面位置：{{ block.bbox.join(', ') }}</small>
            <p v-if="block.text_origin === 'page_context'" class="muted">此图无文字说明；下方文字含同页上下文。</p>
            <pre class="evidence-text">{{ splitText(block)[0] }}<mark v-if="splitText(block)[1]">{{ splitText(block)[1] }}</mark>{{ splitText(block)[2] }}</pre>
            <a v-for="image in block.images.filter(item => isImageResource(item.url))" :key="image.id" :href="image.url" target="_blank" rel="noopener noreferrer"><img :src="image.url" :alt="`第 ${pageNumber} 页${evidenceLabels[block.evidence_type]}证据`" loading="lazy" /></a>
          </article>
        </aside>
      </div>
    </template>
  </section>
</template>
