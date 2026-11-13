import type { Page, Route } from '@playwright/test'
import type { DocumentRecord, TaskStatus } from '../src/types/document'
import type { Conversation, ImageResource, Message, SourceReference } from '../src/types/chat'

export const timestamp = '2026-11-13T07:00:00Z'
export const documentId = '22222222-2222-4222-8222-222222222222'
export const conversation: Conversation = { id: '11111111-1111-4111-8111-111111111111',
  title: '测试会话', created_at: timestamp, updated_at: timestamp }
export const capabilities = { vision_enabled: true, upload_max_files: 10,
  upload_max_file_bytes: 52428800, upload_max_request_bytes: 209715200,
  image_max_upload_files: 4, image_max_file_bytes: 10485760 }
export const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1sAAAAASUVORK5CYII=', 'base64')
export const image: ImageResource = { id: '33333333-3333-4333-8333-333333333333',
  kind: 'user_upload', url: '/api/images/33333333-3333-4333-8333-333333333333/content', media_type: 'image/png',
  width: 1, height: 1, size_bytes: png.length, document_id: null, version: null, page: null }
export const source: SourceReference = { source_id: 'S1', document_id: documentId, version: 1,
  filename: '教材.pdf', page: 2, chunk_id: `${documentId}:1:2:0:0`, image_ids: [image.id],
  images: [{ ...image, kind: 'document_image', document_id: documentId, version: 1, page: 2 }] }
export function document(status: TaskStatus = 'completed', id = documentId, filename = '教材.pdf'): DocumentRecord {
  return { id, original_filename: filename, size_bytes: 1234, latest_version: 1,
    current_searchable_version: status === 'completed' ? 1 : null, created_at: timestamp, updated_at: timestamp,
    latest_task: { id: '44444444-4444-4444-8444-444444444444', document_id: id, version: 1,
      status, stage: status === 'processing' ? 'parsing' : status === 'pending' ? 'queued' : status === 'completed' ? 'done' : 'failed',
      attempts: status === 'pending' ? 0 : 1, max_retries: 3, error: status === 'failed'
        ? { code: 'MINERU_TIMEOUT', message: 'PDF 解析超时', retryable: true, details: {} } : null,
      next_attempt_at: null, created_at: timestamp, updated_at: timestamp },
  }
}
export const paged = <T>(items: T[], total = items.length) => ({ items, total, page: 1, page_size: 100 })
export function message(overrides: Partial<Message> = {}): Message {
  return { id: 'assistant1', conversation_id: conversation.id, sequence: 2, role: 'assistant', status: 'completed',
    content: `## 结论\n**100 件** [S1]\n\n![相关图](${image.url})`, images: [], sources: [source],
    error: null, created_at: timestamp, updated_at: timestamp, ...overrides }
}
export async function basicRoutes(page: Page, custom?: (route: Route, path: string) => Promise<boolean>) {
  await page.route('http://127.0.0.1:5174/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (custom && await custom(route, path)) return
    let json: unknown
    if (path === '/api/health') json = { status: 'ok' }
    else if (path === '/api/capabilities') json = capabilities
    else if (path === '/api/documents') json = paged([document()])
    else if (path === '/api/conversations') json = route.request().method() === 'POST' ? conversation : paged([conversation])
    else if (path.endsWith('/messages')) json = paged<Message>([])
    else if (path.endsWith('/images')) json = { items: [image] }
    else if (path === image.url) { await route.fulfill({ contentType: 'image/png', body: png }); return }
    else { await route.fulfill({ status: 404, json: {} }); return }
    await route.fulfill({ json })
  })
}
