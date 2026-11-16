import type { ErrorDetail } from './common'

export interface Conversation { id: string; title: string; created_at: string; updated_at: string }
export interface ImageResource {
  id: string
  kind: 'user_upload' | 'document_image'
  url: string
  media_type: 'image/png' | 'image/jpeg' | 'image/webp'
  size_bytes: number
  width: number
  height: number
  document_id: string | null
  version: number | null
  page: number | null
}
export interface SourceReference {
  source_id: string
  document_id: string
  version: number
  filename: string
  page: number | null
  chunk_id: string
  evidence_type?: 'text' | 'table' | 'image'
  text_origin?: 'content' | 'page_context'
  block_index?: number | null
  char_offset?: number | null
  char_end?: number | null
  bbox?: number[] | null
  image_relation?: 'block' | 'page' | null
  retrieval_score?: number | null
  rerank_score?: number | null
  image_ids: string[]
  images: ImageResource[]
}
export interface QuestionRequest { text: string; image_ids: string[]; document_ids: string[] | null }
export interface Message {
  id: string
  conversation_id: string
  sequence: number
  role: 'user' | 'assistant'
  status: 'pending' | 'completed' | 'failed'
  content: string
  images: ImageResource[]
  sources: SourceReference[]
  error: ErrorDetail | null
  created_at: string
  updated_at: string
}
export interface AnswerResponse { user_message: Message; assistant_message: Message }
export type Question = QuestionRequest
export type Answer = AnswerResponse
