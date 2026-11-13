<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Capabilities } from '../types/common'
import { formatBytes } from '../utils/format'

const props = defineProps<{ busy: boolean; limits: Capabilities | null }>()
const files = defineModel<File[]>({ required: true })
const emit = defineEmits<{ upload: [files: File[]] }>()
const input = ref<HTMLInputElement>()
const totalBytes = computed(() => files.value.reduce((total, file) => total + file.size, 0))
const error = computed(() => {
  if (!props.limits) return ''
  if (files.value.length > props.limits.upload_max_files) return `每批最多 ${props.limits.upload_max_files} 个 PDF，请移除多余文件。`
  if (totalBytes.value >= props.limits.upload_max_request_bytes) return '所选文件总大小超限，请减少文件后上传（请求还包含表单开销）。'
  return ''
})
function select(event: Event) {
  files.value = Array.from((event.target as HTMLInputElement).files || [])
}
watch(files, () => { if (!files.value.length && input.value) input.value.value = '' })
</script>

<template>
  <form class="upload-box" @submit.prevent="emit('upload', [...files])">
    <h3>批量上传 PDF</h3>
    <p v-if="limits" class="muted">每批最多 {{ limits.upload_max_files }} 个，每个 {{ formatBytes(limits.upload_max_file_bytes) }}，整批请求上限 {{ formatBytes(limits.upload_max_request_bytes) }}。</p>
    <p v-else class="muted">选择多个 PDF；数量和大小由服务器校验。</p>
    <label class="file-picker">选择 PDF 文件
      <input ref="input" type="file" accept=".pdf,application/pdf" multiple :disabled="busy" @change="select" />
    </label>
    <ul v-if="files.length" class="selected-files">
      <li v-for="(file, index) in files" :key="index">
        <span>{{ index + 1 }}. {{ file.name }} <small>{{ formatBytes(file.size) }}</small>
          <span v-if="limits && file.size > limits.upload_max_file_bytes" class="error"> · 超过单文件上限</span>
          <span v-if="!file.name.toLowerCase().endsWith('.pdf')" class="error"> · 非 PDF 文件</span>
        </span>
        <button type="button" class="text-button" :disabled="busy" :aria-label="`移除第 ${index + 1} 个文件`" @click="files = files.filter((_, i) => i !== index)">移除</button>
      </li>
    </ul>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="actions">
      <button type="submit" :disabled="busy || !files.length || !!error">{{ busy ? '正在上传…' : `上传${files.length ? ` ${files.length} 个文件` : ''}` }}</button>
      <span v-if="files.length" class="muted">共 {{ formatBytes(totalBytes) }} · 上传后异步解析</span>
    </div>
  </form>
</template>
