import { describe, expect, it } from 'vitest'
import type { AgentRunResult } from '../../shared/types/api'
import {
  chatReducer,
  conversationHistory,
  currentTurn,
  initialChatState,
} from './chatReducer'

describe('chatReducer', () => {
  it('rebuilds the streamed answer and reconciles the final result', () => {
    let state = chatReducer(initialChatState, {
      type: 'started',
      question: '有多少客户？',
      turnId: 'turn-1',
    })
    state = chatReducer(state, { type: 'delta', delta: '共有 ' })
    state = chatReducer(state, { type: 'delta', delta: '59 位客户。' })

    expect(currentTurn(state)?.status).toBe('streaming')
    expect(currentTurn(state)?.answer).toBe('共有 59 位客户。')

    const result: AgentRunResult = {
      request_id: 'trace_test',
      conversation_id: 'conv_test',
      answer: '共有 59 位客户。',
      selected_tables: ['customers'],
      sql: 'SELECT COUNT(*) FROM customers',
      query_result: null,
      refused: false,
    }
    state = chatReducer(state, { type: 'completed', result })

    expect(currentTurn(state)?.status).toBe('succeeded')
    expect(currentTurn(state)?.answer).toBe(result.answer)
    expect(currentTurn(state)?.requestId).toBe('trace_test')
  })

  it('appends a new turn without clearing conversation history', () => {
    const first = chatReducer(initialChatState, {
      type: 'started',
      question: '旧问题',
      turnId: 'turn-1',
    })
    const next = chatReducer(first, {
      type: 'started',
      question: '新问题',
      turnId: 'turn-2',
    })

    expect(next.turns.map((turn) => turn.question)).toEqual([
      '旧问题',
      '新问题',
    ])
    expect(currentTurn(next)?.status).toBe('running')
  })

  it('tracks the conversation id and clears all turns on reset', () => {
    let state = chatReducer(initialChatState, {
      type: 'started',
      question: '问题',
      turnId: 'turn-1',
    })
    state = chatReducer(state, {
      type: 'meta',
      requestId: 'trace_test',
      conversationId: 'conv_test',
    })

    expect(state.conversationId).toBe('conv_test')
    expect(chatReducer(state, { type: 'reset' })).toEqual(initialChatState)
  })

  it('keeps only the five most recent successful turns as context', () => {
    let state = initialChatState
    for (let index = 0; index < 6; index += 1) {
      state = chatReducer(state, {
        type: 'started',
        question: `问题 ${index}`,
        turnId: `turn-${index}`,
      })
      state = chatReducer(state, {
        type: 'completed',
        result: {
          request_id: `trace-${index}`,
          conversation_id: 'conv-test',
          answer: `回答 ${index}`,
          selected_tables: [],
          sql: `SELECT ${index}`,
          query_result: null,
          refused: false,
        },
      })
    }

    expect(conversationHistory(state).map((turn) => turn.question)).toEqual([
      '问题 1',
      '问题 2',
      '问题 3',
      '问题 4',
      '问题 5',
    ])
  })
})
