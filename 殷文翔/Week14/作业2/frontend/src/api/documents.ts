import { request } from './http'
import type { Page } from '../types/common'
import type { BatchUploadResponse, DocumentRecord, ParseTask, TaskStatus } from '../types/document'

export function listDocuments(page = 1, status?: TaskStatus, signal?: AbortSignal) {
  const query = new URLSearchParams({ page: String(page), page_size: '20' })
  if (status) query.set('status', status)
  return request<Page<DocumentRecord>>(`/documents?${query}`, { signal })
}

export const getDocument = (id: string, signal?: AbortSignal) =>
  request<DocumentRecord>(`/documents/${encodeURIComponent(id)}`, { signal })

export function uploadDocuments(files: File[], signal?: AbortSignal) {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  return request<BatchUploadResponse>('/documents/batch', { method: 'POST', body: form, signal })
}

export const retryDocument = (id: string, version: number, key: string, signal?: AbortSignal) =>
  request<ParseTask>(`/documents/${encodeURIComponent(id)}/retry`, {
    method: 'POST', headers: { 'Idempotency-Key': key },
    body: JSON.stringify({ expected_latest_version: version }), signal,
  })
