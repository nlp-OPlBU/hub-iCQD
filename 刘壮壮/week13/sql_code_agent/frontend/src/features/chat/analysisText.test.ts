import { describe, expect, it } from 'vitest'
import { analysisText } from './analysisText'

describe('analysisText', () => {
  it('removes a multiline markdown table from the main answer', () => {
    const answer = `美国客户最多，共 13 位。

| 国家 | 客户数 |
| --- | ---: |
| USA | 13 |
| Canada | 8 |`

    expect(analysisText(answer)).toBe('美国客户最多，共 13 位。')
  })

  it('removes a compact one-line markdown table', () => {
    const answer =
      '美国客户最多。 | 国家 | 客户数 | |---|---| | USA | 13 |'

    expect(analysisText(answer)).toBe('美国客户最多。')
  })

  it('keeps ordinary analysis text unchanged', () => {
    expect(analysisText('美国客户最多，共 13 位。')).toBe(
      '美国客户最多，共 13 位。',
    )
  })
})
