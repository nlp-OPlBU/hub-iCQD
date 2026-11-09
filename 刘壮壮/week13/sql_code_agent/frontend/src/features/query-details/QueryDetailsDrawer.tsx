import {
  Check,
  ChevronDown,
  Clipboard,
  Database,
  FileCode2,
  MessageSquareText,
  Play,
  ShieldCheck,
  Sparkles,
  Table2,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type {
  AgentRunResult,
  ProgressUpdate,
  QueryResult,
  WorkflowStage,
} from '../../shared/types/api'

type DetailTab = 'data' | 'sql' | 'process'

interface QueryDetailsDrawerProps {
  open: boolean
  result: AgentRunResult
  progress: ProgressUpdate[]
  onClose: () => void
}

function cellValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function ResultTable({ result }: { result: QueryResult }) {
  return (
    <div className="table-scroll">
      <table className="result-table">
        <thead>
          <tr>
            {result.columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((value, columnIndex) => (
                <td key={`${rowIndex}-${columnIndex}`}>
                  {cellValue(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const stagePresentation: Record<
  WorkflowStage,
  { title: string; description: string; icon: LucideIcon }
> = {
  contextualize: {
    title: '理解对话上下文',
    description: '判断当前问题是否需要结合最近的对话才能独立理解。',
    icon: MessageSquareText,
  },
  parse_database: {
    title: '读取数据库结构',
    description: '读取数据表、字段与关联关系，建立可查询的数据目录。',
    icon: Database,
  },
  select_tables: {
    title: '选择相关数据表',
    description: '从数据库目录中定位回答当前问题所需的数据表。',
    icon: Table2,
  },
  generate_sql: {
    title: '生成候选 SQL',
    description: '根据问题与相关表结构生成一条候选查询。',
    icon: FileCode2,
  },
  validate_sql: {
    title: '执行安全校验',
    description: '检查单语句、只读权限、对象引用与 SQLite 编译结果。',
    icon: ShieldCheck,
  },
  rewrite_sql: {
    title: '修复候选 SQL',
    description: '根据预检或执行错误修复查询，并送回安全校验。',
    icon: Wrench,
  },
  execute_sql: {
    title: '执行只读查询',
    description: '在独立只读连接与资源限制下运行通过校验的查询。',
    icon: Play,
  },
  synthesize: {
    title: '整理查询回答',
    description: '只使用数据库返回的事实生成自然语言回答。',
    icon: Sparkles,
  },
}

function formatDuration(duration: number | null | undefined): string | null {
  if (duration === null || duration === undefined) return null
  if (duration < 1) return '< 1 ms'
  if (duration < 1_000) return `${duration.toFixed(1)} ms`
  return `${(duration / 1_000).toFixed(2)} s`
}

function ProcessDetails({
  result,
  progress,
}: {
  result: AgentRunResult
  progress: ProgressUpdate[]
}) {
  const completedSteps = progress.filter((item) => item.status === 'completed')

  if (completedSteps.length === 0) {
    return <p className="empty-detail">暂无可展示的执行过程。</p>
  }

  return (
    <div className="process-view">
      {result.refused && (
        <div className="process-overview blocked" role="status">
          <span className="overview-icon" aria-hidden="true">
            <ShieldCheck size={18} />
          </span>
          <div>
            <strong>请求已在执行前安全拦截</strong>
            <p>没有对数据库执行任何写入。</p>
          </div>
        </div>
      )}

      <ol className="process-list">
        {completedSteps.map((item, index) => {
          const presentation = stagePresentation[item.stage]
          const Icon = presentation.icon
          const duration = formatDuration(item.duration_ms)
          const tone = item.tone ?? 'success'

          return (
            <li
              key={`${item.stage}-${index}`}
              className={`process-step ${tone}`}
            >
              <span className="process-marker" aria-hidden="true">
                <Icon size={15} />
              </span>
              <details className="process-step-card">
                <summary>
                  <span className="process-step-title">
                    {presentation.title}
                  </span>
                  <span className={`process-outcome ${tone}`}>
                    <Check size={12} aria-hidden="true" />
                    {item.message}
                  </span>
                  {duration && <time>{duration}</time>}
                  <ChevronDown
                    className="process-chevron"
                    size={15}
                    aria-hidden="true"
                  />
                </summary>
                <div className="process-step-body">
                  <p className="process-description">
                    {item.description ?? presentation.description}
                  </p>
                  {(item.details?.length ?? 0) > 0 && (
                    <dl className="process-facts">
                      {item.details?.map((detail, detailIndex) => (
                        <div key={`${detail.label}-${detailIndex}`}>
                          <dt>{detail.label}</dt>
                          <dd>
                            {detail.format === 'code' ? (
                              <code>{detail.value}</code>
                            ) : (
                              detail.value
                            )}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              </details>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function QueryDetailsDrawer({
  open,
  result,
  progress,
  onClose,
}: QueryDetailsDrawerProps) {
  const [tab, setTab] = useState<DetailTab>('data')
  const [copied, setCopied] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const copySql = async () => {
    if (!result.sql) return
    await navigator.clipboard.writeText(result.sql)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1_500)
  }

  return (
    <div className="drawer-layer">
      <button
        type="button"
        className="drawer-backdrop"
        aria-label="关闭查询详情"
        onClick={onClose}
      />
      <aside
        className="details-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="details-title"
      >
        <header className="drawer-header">
          <div>
            <p className="section-kicker">查询详情</p>
            <h2 id="details-title">数据与执行信息</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭查询详情"
          >
            <X size={19} aria-hidden="true" />
          </button>
        </header>

        <div className="detail-tabs" role="tablist" aria-label="查询详情视图">
          {(
            [
              ['data', '数据结果'],
              ['sql', 'SQL'],
              ['process', '执行过程'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={tab === value ? 'active' : ''}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="drawer-content">
          {tab === 'data' &&
            (result.query_result ? (
              <>
                <div className="detail-summary">
                  <span>{result.query_result.row_count} 行</span>
                  <span>{result.query_result.elapsed_ms.toFixed(1)} ms</span>
                  {result.query_result.truncated && (
                    <span className="warning-chip">结果已截断</span>
                  )}
                </div>
                <ResultTable result={result.query_result} />
              </>
            ) : (
              <p className="empty-detail">本次请求没有可展示的数据结果。</p>
            ))}

          {tab === 'sql' &&
            (result.sql ? (
              <>
                <div className="code-toolbar">
                  <span>SQLite</span>
                  <button type="button" onClick={copySql}>
                    {copied ? (
                      <Check size={15} aria-hidden="true" />
                    ) : (
                      <Clipboard size={15} aria-hidden="true" />
                    )}
                    {copied ? '已复制' : '复制 SQL'}
                  </button>
                </div>
                <pre className="sql-code">
                  <code>{result.sql}</code>
                </pre>
                <div className="selected-tables">
                  <span>使用的数据表</span>
                  <div>
                    {result.selected_tables.map((table) => (
                      <code key={table}>{table}</code>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <p className="empty-detail">安全拒绝不会展示生成的 SQL。</p>
            ))}

          {tab === 'process' && (
            <ProcessDetails result={result} progress={progress} />
          )}
        </div>
      </aside>
    </div>
  )
}
