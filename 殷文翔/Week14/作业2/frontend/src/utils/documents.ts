import type { SourceReference } from '../types/chat'

export const evidenceLabels = { text: '正文', table: '表格', image: '图片' }

export function documentViewPath(id: string, version?: number | null) {
  return `/documents/${encodeURIComponent(id)}/view${version ? `?version=${version}` : ''}`
}

export function sourceViewPath(source: SourceReference) {
  const query = new URLSearchParams({ version: String(source.version) })
  if (source.page) query.set('page', String(source.page))
  if (source.block_index != null) query.set('block', String(source.block_index))
  if (source.char_offset != null) query.set('offset', String(source.char_offset))
  if (source.char_end != null) query.set('end', String(source.char_end))
  return `/documents/${encodeURIComponent(source.document_id)}/view?${query}`
}
