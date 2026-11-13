<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getHealth } from './api/http'

const route = useRoute()
const status = ref('正在检查 API…')
const tone = computed(() => status.value === 'API 可访问' ? 'ok' : status.value === '正在检查 API…' ? 'checking' : 'error')
const wide = computed(() => route.path === '/chat')
const controller = new AbortController()
onMounted(async () => {
  try {
    const health = await getHealth(controller.signal)
    status.value = health.status === 'ok' ? 'API 可访问' : 'API 状态异常'
  } catch (error) {
    if (!controller.signal.aborted) status.value = 'API 未连接'
  }
})
onBeforeUnmount(() => controller.abort())
</script>

<template>
  <div class="workspace" :class="{ 'workspace-wide': wide }">
    <header class="app-header">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">RAG</span>
        <div class="brand-copy"><p class="eyebrow">MULTIMODAL RAG</p><h1>文档知识库</h1></div>
      </div>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink to="/documents">文档管理</RouterLink>
        <RouterLink to="/chat">图文问答</RouterLink>
      </nav>
      <span class="badge" :class="tone" role="status">{{ status }}</span>
    </header>
    <main><RouterView /></main>
    <footer>回答依据所选文档生成，请结合原文与来源核对。</footer>
  </div>
</template>
