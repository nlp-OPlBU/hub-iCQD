import { useQuery } from '@tanstack/react-query'
import {
  ArrowUpRight,
  Database,
  LockKeyhole,
  RotateCcw,
  Send,
  SlidersHorizontal,
  Sparkles,
  Square,
} from 'lucide-react'
import {
  type FormEvent,
  type KeyboardEvent,
  useState,
} from 'react'
import { QueryDetailsDrawer } from '../query-details/QueryDetailsDrawer'
import { analysisText } from './analysisText'
import { useChatStream } from './useChatStream'
import { currentTurn } from './chatReducer'

const suggestions = [
  {
    label: '客户分布',
    question: '每个国家分别有多少位客户？按客户数量降序排列。',
  },
  {
    label: '销售排行',
    question: '销售额最高的五个国家是哪些？',
  },
  {
    label: '热门歌曲',
    question: '销量最高的十首歌曲是什么？',
  },
  {
    label: '员工表现',
    question: '每位销售支持员工负责的客户数量是多少？',
  },
]

function displayCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function displayColumnName(column: string): string {
  const labels: Record<string, string> = {
    country: '国家',
    customercount: '客户数',
    count: '数量',
    total: '总计',
    totalamount: '总金额',
    revenue: '销售额',
    sales: '销量',
    name: '名称',
    title: '标题',
    artist: '艺术家',
    album: '专辑',
    genre: '流派',
    quantity: '数量',
  }
  const normalized = column.toLowerCase().replaceAll(/[^a-z]/g, '')
  return labels[normalized] ?? column
}

function isNumericColumn(
  rows: Array<Array<unknown | null>>,
  columnIndex: number,
): boolean {
  const values = rows
    .map((row) => row[columnIndex])
    .filter((value) => value !== null && value !== undefined)
  return values.length > 0 && values.every((value) => typeof value === 'number')
}

export function ChatWorkspace() {
  const { state, submit, cancel, reset } = useChatStream()
  const [input, setInput] = useState('')
  const [detailsTurnId, setDetailsTurnId] = useState<string | null>(null)
  const activeTurn = currentTurn(state)
  const detailsTurn =
    state.turns.find((turn) => turn.id === detailsTurnId) ?? null
  const isBusy =
    activeTurn?.status === 'running' || activeTurn?.status === 'streaming'

  const health = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const response = await fetch('/api/health')
      if (!response.ok) throw new Error('服务不可用')
      return response.json() as Promise<{ status: string }>
    },
    retry: 1,
    refetchInterval: 30_000,
  })

  const send = (question: string) => {
    if (isBusy || !question.trim()) return
    setInput('')
    void submit(question)
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    send(input)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send(input)
    }
  }

  const closeDetails = () => {
    setDetailsTurnId(null)
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">
            <Database size={19} aria-hidden="true" />
          </span>
          <div>
            <strong>SQL Code Agent</strong>
            <span>AI 数据问答工作台</span>
          </div>
        </div>
        <div className="header-meta">
          <span className="meta-chip">
            <Database size={14} aria-hidden="true" />
            Chinook
          </span>
          <span className="meta-chip">
            <LockKeyhole size={14} aria-hidden="true" />
            只读模式
          </span>
          <span
            className={`service-state ${health.isSuccess ? 'online' : 'offline'}`}
          >
            <span aria-hidden="true" />
            {health.isSuccess ? '服务正常' : '服务连接中'}
          </span>
        </div>
      </header>

      <main className="workspace">
        {state.turns.length === 0 ? (
          <section className="empty-state" aria-labelledby="workspace-title">
            <span className="hero-icon">
              <Sparkles size={24} aria-hidden="true" />
            </span>
            <p className="section-kicker">自然语言查询</p>
            <h1 id="workspace-title">从数据中获得清晰答案</h1>
            <p className="hero-copy">
              描述你想了解的问题，系统会安全地生成并执行只读 SQL，
              再将结果整理成自然语言。
            </p>
            <div className="suggestions" aria-label="示例问题">
              {suggestions.map((suggestion) => (
                <button
                  type="button"
                  key={suggestion.label}
                  onClick={() => send(suggestion.question)}
                >
                  <span>{suggestion.label}</span>
                  <p>{suggestion.question}</p>
                  <ArrowUpRight size={16} aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>
        ) : (
          <section className="conversation" aria-label="当前对话">
            {state.turns.map((turn, turnIndex) => {
              const isLatest = turnIndex === state.turns.length - 1
              const currentProgress = turn.progress.at(-1)
              return (
                <div className="conversation-turn" key={turn.id}>
                  <div className="user-turn">
                    <span>你的问题</span>
                    <p>{turn.question}</p>
                  </div>

                  {isLatest &&
                    (turn.status === 'running' ||
                      turn.status === 'streaming') && (
                      <div className="progress-card" aria-live="polite">
                        <span
                          className="activity-indicator"
                          aria-hidden="true"
                        />
                        <div>
                          <strong>
                            {turn.status === 'streaming'
                              ? '正在组织回答'
                              : currentProgress?.message || '正在理解问题'}
                          </strong>
                          <p>系统正在安全地查询数据库，请稍候。</p>
                        </div>
                        <button type="button" onClick={cancel}>
                          <Square
                            size={13}
                            fill="currentColor"
                            aria-hidden="true"
                          />
                          停止
                        </button>
                      </div>
                    )}

                  {(turn.answer || turn.status === 'succeeded') && (
                    <article
                      className="answer-card"
                      aria-busy={turn.status === 'streaming'}
                    >
                      <div className="answer-text">
                        {analysisText(turn.answer)}
                        {turn.status === 'streaming' && (
                          <span
                            className="stream-cursor"
                            aria-hidden="true"
                          />
                        )}
                      </div>

                {turn.result?.query_result && (
                  <div className="result-preview">
                    <div className="preview-heading">
                      <div>
                        <strong>结果预览</strong>
                        <span>
                          前{' '}
                          {Math.min(
                            5,
                            turn.result.query_result.rows.length,
                          )}{' '}
                          行 ·{' '}
                          {turn.result.query_result.truncated
                            ? `至少 ${turn.result.query_result.row_count} 行`
                            : `共 ${turn.result.query_result.row_count} 行`}{' '}
                          ·{' '}
                          {turn.result.query_result.elapsed_ms.toFixed(1)} ms
                        </span>
                      </div>
                      {turn.result.query_result.truncated && (
                        <span className="warning-chip">已截断</span>
                      )}
                    </div>
                    <div className="table-scroll">
                      <table className="result-table compact">
                        <thead>
                          <tr>
                            {turn.result.query_result.columns.map(
                              (column, columnIndex) => (
                              <th
                                key={column}
                                scope="col"
                                className={
                                  isNumericColumn(
                                    turn.result!.query_result!.rows,
                                    columnIndex,
                                  )
                                    ? 'numeric'
                                    : undefined
                                }
                              >
                                {displayColumnName(column)}
                              </th>
                              ),
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {turn.result.query_result.rows
                            .slice(0, 5)
                            .map((row, rowIndex) => (
                              <tr key={rowIndex}>
                                {row.map((value, columnIndex) => (
                                  <td
                                    key={`${rowIndex}-${columnIndex}`}
                                    className={
                                      isNumericColumn(
                                        turn.result!.query_result!.rows,
                                        columnIndex,
                                      )
                                        ? 'numeric'
                                        : undefined
                                    }
                                  >
                                    {displayCell(value)}
                                  </td>
                                ))}
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {turn.result &&
                  (turn.result.sql ||
                    turn.result.query_result ||
                    turn.progress.length > 0) && (
                    <footer className="answer-actions">
                      <button
                        type="button"
                        onClick={() => setDetailsTurnId(turn.id)}
                      >
                        <SlidersHorizontal size={16} aria-hidden="true" />
                        查看查询详情
                      </button>
                      {isLatest && (
                        <button type="button" onClick={reset}>
                          <RotateCcw size={16} aria-hidden="true" />
                          新建对话
                        </button>
                      )}
                    </footer>
                  )}
                    </article>
                  )}

                  {isLatest && turn.status === 'failed' && (
                    <div className="feedback-card error" role="alert">
                      <div>
                        <strong>未能完成查询</strong>
                        <p>{turn.error?.message}</p>
                        {turn.error?.request_id && (
                          <code>{turn.error.request_id.slice(-8)}</code>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => void submit(turn.question)}
                      >
                        重试
                      </button>
                    </div>
                  )}

                  {isLatest && turn.status === 'cancelled' && (
                    <div className="feedback-card" role="status">
                      <div>
                        <strong>查询已停止</strong>
                        <p>你可以修改问题后重新发送。</p>
                      </div>
                      <button type="button" onClick={reset}>
                        新建对话
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </section>
        )}
      </main>

      <footer className="composer-region">
        <form className="composer" onSubmit={handleSubmit}>
          <label htmlFor="question-input">询问数据库</label>
          <textarea
            id="question-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="例如：哪个国家的客户数量最多？"
            rows={1}
            maxLength={2_000}
            disabled={isBusy}
          />
          <div className="composer-footer">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <button
              type="submit"
              disabled={isBusy || !input.trim()}
              aria-label="发送问题"
            >
              <Send size={17} aria-hidden="true" />
              发送
            </button>
          </div>
        </form>
        <p className="composer-note">
          AI 生成的查询可能存在偏差，重要结果请结合查询详情核验。
        </p>
      </footer>

      {detailsTurn?.result && (
        <QueryDetailsDrawer
          open
          result={detailsTurn.result}
          progress={detailsTurn.progress}
          onClose={closeDetails}
        />
      )}
    </div>
  )
}
