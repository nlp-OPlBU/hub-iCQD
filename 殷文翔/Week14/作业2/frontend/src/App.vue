<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getHealth } from './api/http'

const status = ref('正在检查 API…')
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
  <div class="workspace">
    <header>
      <div><p class="eyebrow">MULTIMODAL RAG</p><h1>文档知识库</h1></div>
      <span class="badge" role="status">{{ status }}</span>
    </header>
    <nav aria-label="主导航">
      <RouterLink to="/documents">文档管理</RouterLink>
      <RouterLink to="/chat">图文问答</RouterLink>
    </nav>
    <main><RouterView /></main>
    <footer>回答依据所选文档生成，请结合原文与来源核对。</footer>
  </div>
</template>
