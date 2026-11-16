<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Capabilities } from '../types/common'
import { formatBytes } from '../utils/format'

const props = defineProps<{
  busy: boolean; locked: boolean; blocked: boolean; vision: boolean | null; limits: Capabilities | null
}>()
const text = defineModel<string>('text', { required: true })
const files = defineModel<File[]>('files', { required: true })
const emit = defineEmits<{ submit: []; replay: [] }>()
const input = ref<HTMLInputElement>(), previews = ref<string[]>([]), error = ref(''), dragging = ref(false)
const disabled = computed(() => props.busy || props.locked)
const allowedTypes = ['image/png', 'image/jpeg', 'image/webp']
function release() { previews.value.forEach(url => URL.revokeObjectURL(url)); previews.value = [] }
watch(files, value => {
  release(); previews.value = value.map(file => URL.createObjectURL(file))
  if (input.value) input.value.value = ''
  error.value = ''
})
function acceptFiles(selected: File[]) {
  if (!selected.length) return
  if (props.vision === false) {
    error.value = '当前未启用图片问答，请清除图片后使用文字提问。'
  } else if (props.limits && selected.length > props.limits.image_max_upload_files) {
    error.value = `最多选择 ${props.limits.image_max_upload_files} 张图片。`
  } else if (selected.some(file => !allowedTypes.includes(file.type))) {
    error.value = '请选择 PNG、JPEG 或 WebP 图片。'
  } else if (selected.some(file => !file.size || (props.limits && file.size > props.limits.image_max_file_bytes))) {
    error.value = '图片为空或超过单张大小限制，请重新选择。'
  } else {
    files.value = selected; error.value = ''
  }
  if (input.value) input.value.value = ''
}
function select(event: Event) {
  acceptFiles(Array.from((event.target as HTMLInputElement).files || []))
}
function onDragOver(event: DragEvent) {
  if (disabled.value) return
  event.preventDefault()
  dragging.value = true
}
function onDragLeave(event: DragEvent) {
  const current = event.currentTarget as HTMLElement | null
  if (current && event.relatedTarget instanceof Node && current.contains(event.relatedTarget)) return
  dragging.value = false
}
function onDrop(event: DragEvent) {
  dragging.value = false
  if (disabled.value) return
  const dropped = Array.from(event.dataTransfer?.files || [])
  if (!dropped.length) return
  const images = dropped.filter(file => file.type.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(file.name))
  if (!images.length) { error.value = '请选择 PNG、JPEG 或 WebP 图片。'; return }
  acceptFiles(images)
}
function onPaste(event: ClipboardEvent) {
  if (disabled.value) return
  const images = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith('image/'))
  if (!images.length) return
  event.preventDefault()
  acceptFiles(images)
}
function canSubmit() {
  return !props.busy && !props.locked && !props.blocked
    && (!!text.value.trim() || !!files.value.length)
    && !(props.vision === false && !!files.value.length)
}
function submitOnEnter(event: KeyboardEvent) {
  if (event.isComposing || !canSubmit()) return
  event.preventDefault()
  emit('submit')
}
onBeforeUnmount(release)
</script>

<template>
  <form
    class="chat-input"
    :class="{ 'is-dragging': dragging }"
    @submit.prevent="emit('submit')"
    @dragenter.prevent="onDragOver"
    @dragover.prevent="onDragOver"
    @dragleave="onDragLeave"
    @drop.prevent="onDrop"
  >
    <label for="question">问题（可仅上传图片）</label>
    <textarea
      id="question"
      v-model="text"
      rows="4"
      maxlength="20000"
      :disabled="disabled"
      placeholder="输入问题，或直接粘贴 / 拖入截图。例如：这张图与所选文档中的流程有什么关联？"
      @paste="onPaste"
      @keydown.enter.exact="submitOnEnter"
    />
    <label class="file-picker">选择问题图片
      <input ref="input" type="file" accept="image/png,image/jpeg,image/webp" multiple :disabled="disabled || vision === false" @change="select" />
    </label>
    <p v-if="limits" class="muted">支持拖拽或粘贴图片 · 最多 {{ limits.image_max_upload_files }} 张 PNG / JPEG / WebP，单张不超过 {{ formatBytes(limits.image_max_file_bytes) }}。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="image-strip previews">
      <figure v-for="(file, index) in files" :key="index">
        <img :src="previews[index]" alt="待发送图片预览" />
        <figcaption>{{ file.name }}</figcaption>
        <button type="button" class="text-button" :disabled="disabled" :aria-label="`移除图片 ${file.name}`" @click="files = files.filter((_, i) => i !== index)">移除</button>
      </figure>
    </div>
    <div class="actions">
      <button v-if="!locked" type="submit" :disabled="!canSubmit()">{{ busy ? '处理中…' : '发送问题' }}</button>
      <button v-else type="button" :disabled="busy" @click="emit('replay')">{{ busy ? '处理中…' : '使用原请求获取结果' }}</button>
      <button v-if="files.length" type="button" class="secondary" :disabled="disabled" @click="files = []">清除图片</button>
      <span class="muted">Enter 发送 · Shift + Enter 换行</span>
    </div>
    <p v-if="locked" class="muted">原请求结果待确认，使用同一次请求获取结果后可继续提问。</p>
    <div v-if="dragging" class="drop-hint">松开以添加图片</div>
  </form>
</template>
