<script setup lang="ts">
import type { SourceReference } from '../types/chat'
defineProps<{ sources: SourceReference[] }>()
</script>

<template>
  <details v-if="sources.length" class="sources" open>
    <summary>文档来源（{{ sources.length }}）</summary>
    <article v-for="source in sources" :key="source.source_id">
      <strong>[{{ source.source_id }}] {{ source.filename }}</strong>
      <p>第 {{ source.page ?? '未知' }} 页 · 版本 {{ source.version }} · 文档 {{ source.document_id }}</p>
      <small>分块：{{ source.chunk_id }}</small>
      <div class="image-strip">
        <a v-for="image in source.images" :key="image.id" :href="image.url" target="_blank" rel="noopener noreferrer">
          <img :src="image.url" :alt="`${source.filename} 第${source.page}页相关图片`" loading="lazy" />
        </a>
      </div>
    </article>
  </details>
</template>
