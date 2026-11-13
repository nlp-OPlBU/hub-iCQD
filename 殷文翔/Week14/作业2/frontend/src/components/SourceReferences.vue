<script setup lang="ts">
import type { SourceReference } from '../types/chat'
import { isImageResource } from '../utils/resources'
defineProps<{ sources: SourceReference[]; referencePrefix: string }>()
</script>

<template>
  <details v-if="sources.length" class="sources" open>
    <summary>文档来源（{{ sources.length }}）</summary>
    <article v-for="(source, index) in sources" :id="`${referencePrefix}-source-${index}`" :key="source.source_id" tabindex="-1">
      <strong>[{{ source.source_id }}] {{ source.filename }}</strong>
      <p>第 {{ source.page ?? '未知' }} 页 · 版本 {{ source.version }} · 文档 {{ source.document_id }}</p>
      <small>分块：{{ source.chunk_id }}</small>
      <div class="image-strip">
        <a v-for="image in source.images.filter(item => isImageResource(item.url))" :key="image.id" :href="image.url" target="_blank" rel="noopener noreferrer">
          <img :src="image.url" :alt="`${source.filename} ${source.page ? `第${source.page}页` : '页码未知'}相关图片`" loading="lazy" />
        </a>
      </div>
    </article>
  </details>
</template>
