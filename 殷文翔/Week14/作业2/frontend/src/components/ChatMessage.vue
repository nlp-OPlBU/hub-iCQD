<script setup lang="ts">
import { computed, useId } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { Message } from '../types/chat'
import SourceReferences from './SourceReferences.vue'
import { isImageResource } from '../utils/resources'

const props = defineProps<{ message: Message }>()
const referencePrefix = useId()
const images = computed(() => props.message.images.filter(image => isImageResource(image.url)))
const html = computed(() => {
  const allowed = new Set([
    ...props.message.images.map(image => image.url),
    ...props.message.sources.flatMap(source => source.images.map(image => image.url)),
  ])
  const clean = DOMPurify.sanitize(marked.parse(props.message.content, { async: false }), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
      'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'img', 'a'],
    ALLOWED_ATTR: ['src', 'alt', 'href', 'title'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  })
  const template = document.createElement('template')
  template.innerHTML = clean
  template.content.querySelectorAll('img').forEach(image => {
    const url = image.getAttribute('src') || ''
    if (!allowed.has(url) || !isImageResource(url)) image.remove()
  })
  template.content.querySelectorAll('a').forEach(link => {
    const url = link.getAttribute('href') || ''
    if (!allowed.has(url) || !isImageResource(url)) link.removeAttribute('href')
  })
  // Build citation links from validated source objects after sanitizing model HTML.
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  nodes.forEach(node => {
    if (node.parentElement?.closest('a, code, pre')) return
    const fragment = document.createDocumentFragment()
    let start = 0
    for (const match of node.data.matchAll(/\[(S[1-9][0-9]*)\]/g)) {
      const index = props.message.sources.findIndex(source => source.source_id === match[1])
      if (index < 0) continue
      fragment.append(node.data.slice(start, match.index))
      const link = document.createElement('a')
      link.href = `#${referencePrefix}-source-${index}`
      link.dataset.sourceTarget = `${referencePrefix}-source-${index}`
      link.textContent = match[0]; link.className = 'citation'
      link.setAttribute('aria-label', `查看来源 ${match[1]}`)
      fragment.append(link); start = match.index! + match[0].length
    }
    if (start) { fragment.append(node.data.slice(start)); node.replaceWith(fragment) }
  })
  return template.innerHTML
})
function openSource(event: MouseEvent) {
  const link = (event.target as Element).closest<HTMLAnchorElement>('a[data-source-target]')
  if (!link) return
  event.preventDefault()
  const target = document.getElementById(link.dataset.sourceTarget || '')
  const details = target?.closest('details')
  if (details) details.open = true
  target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); target?.focus({ preventScroll: true })
}
</script>

<template>
  <article class="chat-message" :class="message.role">
    <strong>{{ message.role === 'user' ? '你' : '文档助手' }}</strong>
    <p v-if="message.status === 'pending'" role="status">正在检索并生成回答…可刷新会话查看进度。</p>
    <p v-else-if="message.error" role="alert">{{ message.error.message }}（{{ message.error.code }}）</p>
    <div class="markdown" @click="openSource" v-html="html" />
    <div class="image-strip">
      <a v-for="image in images" :key="image.id" :href="image.url" target="_blank" rel="noopener noreferrer"><img :src="image.url" alt="用户原图" loading="lazy" /></a>
    </div>
    <p v-if="message.role === 'assistant' && message.status === 'completed' && !message.sources.length" class="muted">本次回答没有可引用的文档来源，请补充资料或调整问题。</p>
    <SourceReferences :sources="message.sources" :reference-prefix="referencePrefix" />
  </article>
</template>
