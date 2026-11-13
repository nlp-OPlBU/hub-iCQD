import type { ErrorDetail } from './common'

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed'
export interface ParseTask {
  id: string
  document_id: string
  version: number
  status: TaskStatus
  stage: 'queued' | 'parsing' | 'persisting' | 'indexing' | 'done' | 'failed'
  attempts: number
  max_retries: number
  error: ErrorDetail | null
  next_attempt_at: string | null
  created_at: string
  updated_at: string
}
export interface DocumentRecord {
  id: string
  original_filename: string
  size_bytes: number
  latest_version: number
  current_searchable_version: number | null
  latest_task: ParseTask
  created_at: string
  updated_at: string
}
export type UploadResult =
  | { index: number; filename: string; outcome: 'accepted'; status_code: 202; document: DocumentRecord }
  | { index: number; filename: string; outcome: 'rejected'; status_code: 413 | 415 | 422 | 503; error: ErrorDetail }
export interface BatchUploadResponse {
  request_id: string
  accepted: number
  rejected: number
  results: UploadResult[]
}
export interface RetryRequest { expected_latest_version: number }

export interface EvidenceBlock {
  block_index: number
  evidence_type: 'text' | 'table' | 'image'
  text_origin: 'content' | 'page_context'
  text: string
  bbox: number[] | null
  images: import('./chat').ImageResource[]
}
export interface DocumentPage {
  document_id: string
  version: number
  filename: string
  page: number
  page_count: number
  blocks: EvidenceBlock[]
}
