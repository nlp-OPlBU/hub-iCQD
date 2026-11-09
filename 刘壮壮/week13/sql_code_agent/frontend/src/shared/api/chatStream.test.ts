import { describe, expect, it } from 'vitest'
import { parseSseBlock } from './chatStream'

describe('parseSseBlock', () => {
  it('parses answer deltas with unicode content', () => {
    expect(
      parseSseBlock('event: answer_delta\ndata: {"delta":"共有 59 位客户。"}'),
    ).toEqual({
      type: 'answer_delta',
      data: { delta: '共有 59 位客户。' },
    })
  })

  it('supports CRLF and ignores unknown event types', () => {
    expect(
      parseSseBlock('event: progress\r\ndata: {"stage":"execute_sql"}'),
    ).toEqual({
      type: 'progress',
      data: { stage: 'execute_sql' },
    })
    expect(parseSseBlock('event: ping\ndata: {}')).toBeNull()
  })
})
