import { request } from './http'
import type { Page } from '../types/common'
import type { Answer, Conversation, ImageResource, Message, Question } from '../types/chat'
import type { DocumentRecord } from '../types/document'

export const createConversation = (signal?: AbortSignal) => request<Conversation>('/conversations', {
  method: 'POST', body: JSON.stringify({ title: '文档问答' }), signal,
})
export const listConversations = (signal?: AbortSignal) => request<Page<Conversation>>('/conversations?page_size=100', { signal })
export const listMessages = (id: string, page = 1, signal?: AbortSignal) =>
  request<Page<Message>>(`/conversations/${id}/messages?page=${page}&page_size=100`, { signal })
export const listDocuments = (page: number, signal?: AbortSignal) =>
  request<Page<DocumentRecord>>(`/documents?page=${page}&page_size=100`, { signal })
export async function uploadImages(id: string, files: File[], signal?: AbortSignal) {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  return request<{ items: ImageResource[] }>(`/conversations/${id}/images`, { method: 'POST', body: form, signal })
}
export const ask = (id: string, question: Question, key: string, signal?: AbortSignal) =>
  request<Answer>(`/conversations/${id}/messages`, {
    method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(question), signal,
  })
