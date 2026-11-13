<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ImageResource, SourceReference } from '../types/chat'
import { sourceViewPath, evidenceLabels } from '../utils/documents'
import { isImageResource } from '../utils/resources'

const props = defineProps<{ sources: SourceReference[]; referencePrefix: string }>()
const expanded = ref(props.sources.length <= 1)
const lightbox = ref<ImageResource | null>(null)
watch(() => props.sources, value => { expanded.value = value.length <= 1 })
function imagesOf(source: SourceReference) {
  return source.images.filter(item => isImageResource(item.url))
}
</script>

<template>
  <section v-if="sources.length" class="sources" aria-label="文档来源">
    <button type="button" class="sources-toggle" :aria-expanded="expanded" @click="expanded = !expanded">
      <span>文档来源（{{ sources.length }}）</span>
      <span class="toggle-hint">{{ expanded ? '收起' : '展开' }}</span>
    </button>
    <div v-if="expanded" class="sources-list">
      <article v-for="(source, index) in sources" :id="`${referencePrefix}-source-${index}`" :key="source.source_id" tabindex="-1">
        <RouterLink :to="sourceViewPath(source)" target="_blank" rel="noopener noreferrer"><strong>[{{ source.source_id }}] {{ source.filename }}</strong> ↗</RouterLink>
        <p>第 {{ source.page ?? '未知' }} 页 · 版本 {{ source.version }} · 文档 {{ source.document_id }}</p>
        <p v-if="source.block_index != null">{{ evidenceLabels[source.evidence_type || 'text'] }} · 原文块 {{ source.block_index + 1 }}<span v-if="source.char_offset != null && source.char_end != null"> · 字符 {{ source.char_offset }}–{{ source.char_end }}</span></p>
        <p v-if="source.image_relation === 'page'" class="muted">下方图片为同页上下文。</p>
        <small>分块：{{ source.chunk_id }}</small>
        <div v-if="imagesOf(source).length" class="image-strip">
          <button
            v-for="image in imagesOf(source)"
            :key="image.id"
            type="button"
            class="thumb"
            :aria-label="`预览 ${source.filename} 相关图片`"
            @click="lightbox = image"
          >
            <img :src="image.url" :alt="`${source.filename} ${source.page ? `第${source.page}页` : '页码未知'}相关图片`" loading="lazy" />
          </button>
        </div>
      </article>
    </div>
    <Teleport to="body">
      <div v-if="lightbox" class="lightbox" role="dialog" aria-modal="true" aria-label="图片预览" @click.self="lightbox = null">
        <button type="button" class="lightbox-close" @click="lightbox = null">关闭</button>
        <img :src="lightbox.url" alt="来源图片预览" />
      </div>
    </Teleport>
  </section>
</template>
