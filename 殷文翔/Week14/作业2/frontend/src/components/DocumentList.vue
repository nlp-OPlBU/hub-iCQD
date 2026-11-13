<script setup lang="ts">
import type { DocumentRecord } from '../types/document'
import { formatBytes, formatTime } from '../utils/format'
import ParseStatus from './ParseStatus.vue'

defineProps<{
  documents: DocumentRecord[]
  retrying: string[]
  unresolved: string[]
  retryErrors: Record<string, string>
}>()
defineEmits<{ retry: [document: DocumentRecord] }>()
const stages: Record<string, string> = {
  queued: '等待执行', parsing: 'PDF 解析', persisting: '保存解析产物', indexing: '建立检索索引', done: '处理完成', failed: '处理失败',
}
</script>

<template>
  <div class="document-list">
    <article v-for="item in documents" :key="item.id" class="document-card">
      <div class="document-heading">
        <div><h3>{{ item.original_filename }}</h3><small>{{ formatBytes(item.size_bytes) }} · 上传于 {{ formatTime(item.created_at) }}</small></div>
        <ParseStatus :status="item.latest_task.status" />
      </div>
      <p class="muted">最新解析 v{{ item.latest_version }} · {{ stages[item.latest_task.stage] || item.latest_task.stage }} · 已执行 {{ item.latest_task.attempts }} / {{ item.latest_task.max_retries + 1 }} 次</p>
      <p v-if="item.current_searchable_version !== null" class="success">可检索版本 v{{ item.current_searchable_version }} · 解析结果已保存，可在问答中查看相关内容和来源。</p>
      <p v-else class="muted">尚无可检索版本，完成解析和索引后可提问。</p>
      <p v-if="item.latest_task.error" class="error">{{ item.latest_task.status === 'failed' ? '失败原因' : '最近一次错误' }}：{{ item.latest_task.error.message }}（{{ item.latest_task.error.code }}）</p>
      <p v-if="item.latest_task.next_attempt_at && item.latest_task.status === 'pending'" class="muted">计划自动重试：{{ formatTime(item.latest_task.next_attempt_at) }}</p>
      <p v-if="retryErrors[item.id]" class="error" role="alert">{{ retryErrors[item.id] }}</p>
      <div class="actions">
        <RouterLink v-if="item.current_searchable_version !== null" class="button-link" :to="{ path: '/chat', query: { document: item.id } }" :aria-label="`向 ${item.original_filename} 提问`">基于此文档提问 →</RouterLink>
        <button v-if="item.latest_task.status === 'failed' || unresolved.includes(item.id)" :disabled="retrying.includes(item.id)" @click="$emit('retry', item)">
          {{ retrying.includes(item.id) ? '正在提交…' : unresolved.includes(item.id) ? '获取重试结果' : '重试解析' }}
        </button>
        <details class="task-details">
          <summary>任务详情</summary>
          <dl>
            <dt>文档 ID</dt><dd>{{ item.id }}</dd>
            <dt>任务 ID</dt><dd>{{ item.latest_task.id }}</dd>
            <dt>任务更新时间</dt><dd>{{ formatTime(item.latest_task.updated_at) }}</dd>
          </dl>
        </details>
      </div>
    </article>
  </div>
</template>
