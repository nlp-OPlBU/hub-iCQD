import type { ChatRequest, ChatStreamEvent } from '../types/api'

export function parseSseBlock(block: string): ChatStreamEvent | null {
  const lines = block.replaceAll('\r\n', '\n').split('\n')
  const eventLine = lines.find((line) => line.startsWith('event:'))
  const dataLines = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())

  if (!eventLine || dataLines.length === 0) {
    return null
  }

  const type = eventLine.slice(6).trim()
  if (
    !['meta', 'progress', 'answer_delta', 'result', 'error'].includes(type)
  ) {
    return null
  }

  return {
    type,
    data: JSON.parse(dataLines.join('\n')),
  } as ChatStreamEvent
}

export async function streamChat(
  request: ChatRequest,
  signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })

  if (!response.ok) {
    throw new Error(
      response.status === 422
        ? '请输入有效的问题。'
        : `请求失败（${response.status}）`,
    )
  }
  if (!response.body) {
    throw new Error('浏览器未收到响应流。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replaceAll('\r\n', '\n')

    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const event = parseSseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (event) onEvent(event)
      boundary = buffer.indexOf('\n\n')
    }

    if (done) break
  }

  if (buffer.trim()) {
    const event = parseSseBlock(buffer)
    if (event) onEvent(event)
  }
}
