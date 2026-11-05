import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type {
  AgentRunResult,
  ProgressUpdate,
} from '../../shared/types/api'
import { QueryDetailsDrawer } from './QueryDetailsDrawer'

const result: AgentRunResult = {
  request_id: 'trace_test',
  conversation_id: 'conv_test',
  answer: '共有 59 位客户。',
  selected_tables: ['customers'],
  sql: 'SELECT COUNT(*) FROM customers',
  query_result: {
    columns: ['COUNT(*)'],
    rows: [[59]],
    row_count: 1,
    truncated: false,
    elapsed_ms: 0.8,
  },
  refused: false,
}

const progress: ProgressUpdate[] = [
  {
    stage: 'select_tables',
    status: 'completed',
    message: '相关数据表已确定',
    description: '已定位回答问题所需的数据表。',
    duration_ms: 12.4,
    tone: 'success',
    details: [
      {
        label: '选中数据表',
        value: 'customers',
        format: 'code',
      },
    ],
  },
  {
    stage: 'validate_sql',
    status: 'completed',
    message: '校验通过',
    description: '候选 SQL 已通过安全预检。',
    duration_ms: 0.4,
    tone: 'success',
    details: [
      {
        label: '校验类别',
        value: 'OK',
        format: 'code',
      },
    ],
  },
]

describe('QueryDetailsDrawer process view', () => {
  it('shows detailed completed steps with facts and durations', () => {
    render(
      <QueryDetailsDrawer
        open
        result={result}
        progress={progress}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '执行过程' }))

    const tableSelectionTitle = screen.getByText('选择相关数据表')
    const tableSelection = tableSelectionTitle.closest('details')

    expect(tableSelection).not.toHaveAttribute('open')
    fireEvent.click(tableSelectionTitle.closest('summary')!)
    expect(tableSelection).toHaveAttribute('open')
    expect(screen.getByText('执行安全校验')).toBeInTheDocument()
    expect(screen.getByText('customers')).toBeInTheDocument()
    expect(screen.getByText('12.4 ms')).toBeInTheDocument()
    expect(screen.getByText('校验通过')).toBeInTheDocument()
    expect(screen.queryByText(/步骤 1/)).not.toBeInTheDocument()
    expect(screen.queryByText('select_tables')).not.toBeInTheDocument()
  })
})
