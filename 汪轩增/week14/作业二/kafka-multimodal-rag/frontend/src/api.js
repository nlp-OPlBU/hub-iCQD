import axios from 'axios'

// 默认走 Vite 代理（相对路径）；跨机器部署时可设置 VITE_API_BASE_URL
const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export const http = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
})

export async function uploadDocuments(files) {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  const { data } = await http.post('/api/documents/upload', form)
  return data
}

export async function listDocuments(status) {
  const { data } = await http.get('/api/documents', {
    params: status ? { status } : {},
  })
  return data
}

export async function deleteDocument(id) {
  const { data } = await http.delete(`/api/documents/${id}`)
  return data
}

export async function retryDocument(id) {
  const { data } = await http.post(`/api/documents/${id}/retry`)
  return data
}

export async function sendChat({ question, files = [], docIds = [], topK }) {
  const form = new FormData()
  form.append('question', question)
  if (topK) form.append('top_k', String(topK))
  if (docIds && docIds.length) form.append('doc_ids', docIds.join(','))
  files.forEach((file) => form.append('images', file))

  const { data } = await http.post('/api/chat', form, { timeout: 180000 })
  return data
}

export function apiError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || JSON.stringify(item))
      .join('；')
  }
  if (detail) return JSON.stringify(detail)
  return error?.message || '请求失败，请稍后重试'
}
