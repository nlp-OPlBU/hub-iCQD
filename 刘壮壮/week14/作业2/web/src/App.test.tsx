import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import PdfPreview from './PdfPreview'

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: () => ({
    promise: Promise.resolve({
      numPages: 10,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({
          width: 595 * scale,
          height: 841 * scale,
        }),
        render: () => ({ promise: Promise.resolve(), cancel: () => {} }),
      }),
    }),
    destroy: () => Promise.resolve(),
  }),
}))

const mockApi = {
  documents: vi.fn(async () => ({ items: [] })),
  conversations: vi.fn(async () => ({ items: [] })),
  messages: vi.fn(async () => ({ items: [] })),
  settings: vi.fn(async () => ({ style: 'concise', depth: 'standard', language: 'zh-CN', instruction: '' })),
  stats: vi.fn(async () => ({ questions: 0, answers: 0, tokens: 0, input_tokens: 0, output_tokens: 0, llm_failures: 0 })),
  knowledgeStats: vi.fn(async () => ({ total: 0, completed: 0, processing: 0, failed: 0, bytes: 0 })),
  health: vi.fn(async (): Promise<{ services: Record<string, string> }> => ({ services: { sqlite: 'ok' } })),
  createConversation: vi.fn(async () => ({ conversation_uid: 'c1', title: 'New', is_pinned: 0, updated_at: '' })),
  upload: vi.fn(async () => ({})),
  deleteDocument: vi.fn(async () => ({})),
  deleteConversation: vi.fn(async () => ({})),
  reparse: vi.fn(async () => ({})),
  searchConversations: vi.fn(async () => ({ items: [] })),
  saveSettings: vi.fn(async () => ({})),
  pin: vi.fn(async () => ({})),
  clearKnowledge: vi.fn(async () => ({})),
  clearConversations: vi.fn(async () => ({})),
  feedback: vi.fn(async () => ({})),
  exportConversation: vi.fn(async () => new Blob()),
}

vi.mock('./api', () => ({
  API: '',
  api: new Proxy(
    {},
    {
      get: (_, prop: string) => (mockApi as Record<string, unknown>)[prop],
    }
  ),
  streamQuestion: async () => {},
}))

describe('knowledge workspace', () => {
  it('shows the empty knowledge workspace and PDF upload action', async () => {
    mockApi.documents.mockResolvedValueOnce({ items: [] })
    mockApi.conversations.mockResolvedValueOnce({ items: [] })
    render(<App />)
    expect(await screen.findByRole('heading', { name: /让文档里的知识/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /上传 PDF/ })).toBeInTheDocument()
    expect(screen.getByText('上传 PDF 开始构建知识库')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /总结文档核心内容/ })).toBeInTheDocument()
  })

  it('auto-scrolls highlighted citation near the upper PDF viewport (not top=0 or center)', async () => {
    const { container } = render(
      <PdfPreview
        uid="doc_1"
        page={4}
        locations={[
          {
            page: 4,
            page_width: 595,
            page_height: 841,
            bbox: [45, 620, 537, 655],
            text: '构建高可用 RAG架构',
          },
        ]}
      />
    )

    const viewer = container.querySelector('.pdf-viewer') as HTMLDivElement
    Object.defineProperty(viewer, 'clientHeight', { value: 700, configurable: true })
    Object.defineProperty(viewer, 'clientWidth', { value: 960, configurable: true })

    await waitFor(() => {
      expect(viewer.dataset.located).toBe('true')
    })

    const highlight = container.querySelector('.pdf-highlight') as HTMLDivElement
    expect(highlight).toBeTruthy()
    const highlightTop = parseFloat(highlight.style.top)
    const highlightWidth = parseFloat(highlight.style.width)
    expect(highlightTop).toBeGreaterThan(900)
    expect(highlightWidth).toBeGreaterThan(700)

    // Visual offset from top of PDF viewport = 24px padding + highlightTop - scrollTop
    const visualTop = 24 + highlightTop - viewer.scrollTop
    expect(viewer.scrollTop).toBeGreaterThan(800)
    // Should sit near upper area (~9% of 700px ≈ 63px), neither at 0px nor centered (~350px)
    expect(visualTop).toBeGreaterThanOrEqual(45)
    expect(visualTop).toBeLessThanOrEqual(90)
  })

  it('opens PDF preview and locates highlight when clicking inline [1] citation', async () => {
    const user = userEvent.setup()
    mockApi.conversations.mockResolvedValueOnce({
      items: [{ conversation_uid: 'c1', title: 'RAG 场景', is_pinned: 0, updated_at: '' }] as any,
    })
    mockApi.messages.mockResolvedValueOnce({
      items: [
        {
          message_uid: 'm1',
          role: 'assistant',
          content: '绝大部分场景都是问答 [1]。',
          status: 'completed',
          sources: [
            {
              document_uid: 'doc_1',
              document_name: 'demo.pdf',
              page_number: 4,
              chunk_id: '4_chunk_1',
              content_snippet: '大模型落地绝大部分场景都是问答；',
              asset_id: null,
              locations: [{ page: 4, page_width: 595, page_height: 841, bbox: [55, 297, 318, 314], text: '大模型落地绝大部分场景都是问答；' }],
            },
          ],
        },
      ] as any,
    })

    const { container } = render(<App />)
    const citeLink = await screen.findByRole('link', { name: /1/ })
    await user.click(citeLink)

    const viewer = container.querySelector('.pdf-viewer') as HTMLDivElement
    await waitFor(() => {
      expect(viewer?.dataset.located).toBe('true')
    })
    expect(viewer.scrollTop).toBeGreaterThan(350)
  })

  it('toggles top-bar system overview popover with metrics, refresh, and Escape dismissal', async () => {
    const user = userEvent.setup()
    mockApi.stats
      .mockResolvedValueOnce({ questions: 3, answers: 3, tokens: 480, input_tokens: 300, output_tokens: 180, llm_failures: 0 })
      .mockResolvedValueOnce({ questions: 3, answers: 3, tokens: 480, input_tokens: 300, output_tokens: 180, llm_failures: 0 })
      .mockResolvedValueOnce({ questions: 4, answers: 4, tokens: 620, input_tokens: 390, output_tokens: 230, llm_failures: 0 })
    mockApi.knowledgeStats.mockResolvedValue({ total: 2, completed: 2, processing: 0, failed: 0, bytes: 2097152 })
    mockApi.health.mockResolvedValue({
      services: { sqlite: 'ok', elasticsearch: 'ok', mineru: 'ok', llm: 'ok', kafka: 'ok' },
    })

    render(<App />)

    const trigger = await screen.findByRole('button', { name: /系统概况/ })
    await waitFor(() => {
      expect(trigger).toHaveTextContent('服务正常 5/5')
      expect(trigger).toHaveTextContent('Token 480')
    })
    expect(screen.queryByRole('dialog', { name: '系统概况' })).not.toBeInTheDocument()

    await user.click(trigger)
    const popover = await screen.findByRole('dialog', { name: '系统概况' })
    expect(popover).toHaveTextContent('系统运行概况')
    expect(popover).toHaveTextContent('2 / 2 篇')
    expect(popover).toHaveTextContent('输入 300 · 输出 180')
    expect(popover).toHaveTextContent('5/5 运行正常')

    await user.click(screen.getByRole('button', { name: /刷新/ }))
    await waitFor(() => {
      expect(popover).toHaveTextContent('620')
      expect(popover).toHaveTextContent('输入 390 · 输出 230')
    })

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '系统概况' })).not.toBeInTheDocument()
  })
})
