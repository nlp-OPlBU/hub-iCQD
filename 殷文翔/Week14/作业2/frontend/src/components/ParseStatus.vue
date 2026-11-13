<script setup lang="ts">
import type { TaskStatus } from '../types/document'
defineProps<{ status: TaskStatus }>()
const labels: Record<TaskStatus, string> = {
  pending: '待解析', processing: '解析中', completed: '解析完成', failed: '解析失败',
}
</script>

<template><span class="task-status" :class="status">{{ labels[status] }}</span></template>
