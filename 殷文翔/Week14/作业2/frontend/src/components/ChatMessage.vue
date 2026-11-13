<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { Message } from '../types/chat'
import SourceReferences from './SourceReferences.vue'

const props = defineProps<{ message: Message }>()
const html = computed(() => {
  const allowed = new Set([
    ...props.message.images.map(image => image.url),
    ...props.message.sources.flatMap(source => source.images.map(image => image.url)),
  ])
  const clean = DOMPurify.sanitize(marked.parse(props.message.content, { async: false }), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
      'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'img', 'a'],
    ALLOWED_ATTR: ['src', 'alt', 'href', 'title'],
  })
  const template = document.createElement('template')
  template.innerHTML = clean
  template.content.querySelectorAll('img').forEach(image => {
    if (!allowed.has(image.getAttribute('src') || '') ||
        !/^\/api\/images\/[0-9a-f-]{36}\/content$/.test(image.getAttribute('src') || '')) image.remove()
  })
  template.content.querySelectorAll('a').forEach(link => {
    if (!allowed.has(link.getAttribute('href') || '')) link.removeAttribute('href')
  })
  return template.innerHTML
})
</script>

<template>
  <article class="chat-message" :class="message.role">
    <strong>{{ message.role === 'user' ? '你' : '文档助手' }}</strong>
    <p v-if="message.status === 'pending'" role="status">正在检索并生成回答…可刷新会话查看进度。</p>
    <p v-else-if="message.error" role="alert">{{ message.error.message }}（{{ message.error.code }}）</p>
    <div class="markdown" v-html="html" />
    <div class="image-strip">
      <img v-for="image in message.images" :key="image.id" :src="image.url" alt="用户原图" />
    </div>
    <SourceReferences :sources="message.sources" />
  </article>
</template>
