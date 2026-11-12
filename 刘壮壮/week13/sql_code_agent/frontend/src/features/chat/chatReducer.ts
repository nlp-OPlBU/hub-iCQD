import type {
  AgentRunResult,
  ApiError,
  ConversationTurnContext,
  ProgressUpdate,
} from '../../shared/types/api'

export type ChatStatus =
  | 'idle'
  | 'running'
  | 'streaming'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface ChatTurn {
  id: string
  status: ChatStatus
  question: string
  requestId: string | null
  answer: string
  progress: ProgressUpdate[]
  result: AgentRunResult | null
  error: ApiError | null
}

export interface ChatState {
  conversationId: string | null
  turns: ChatTurn[]
}

export type ChatAction =
  | { type: 'started'; question: string; turnId: string }
  | { type: 'meta'; requestId: string; conversationId: string }
  | { type: 'progress'; progress: ProgressUpdate }
  | { type: 'delta'; delta: string }
  | { type: 'completed'; result: AgentRunResult }
  | { type: 'failed'; error: ApiError }
  | { type: 'cancelled' }
  | { type: 'reset' }

export const initialChatState: ChatState = {
  conversationId: null,
  turns: [],
}

export function currentTurn(state: ChatState): ChatTurn | null {
  return state.turns.at(-1) ?? null
}

export function conversationHistory(
  state: ChatState,
): ConversationTurnContext[] {
  return state.turns
    .filter(
      (turn) =>
        turn.status === 'succeeded' &&
        turn.result !== null &&
        turn.answer.length > 0,
    )
    .slice(-5)
    .map((turn) => ({
      question: turn.question,
      answer: turn.answer,
      sql: turn.result?.sql ?? null,
    }))
}

function updateCurrent(
  state: ChatState,
  update: (turn: ChatTurn) => ChatTurn,
): ChatState {
  if (state.turns.length === 0) return state
  return {
    ...state,
    turns: state.turns.map((turn, index) =>
      index === state.turns.length - 1 ? update(turn) : turn,
    ),
  }
}

export function chatReducer(
  state: ChatState,
  action: ChatAction,
): ChatState {
  switch (action.type) {
    case 'started':
      return {
        ...state,
        turns: [
          ...state.turns,
          {
            id: action.turnId,
            status: 'running',
            question: action.question,
            requestId: null,
            answer: '',
            progress: [],
            result: null,
            error: null,
          },
        ],
      }
    case 'meta':
      return updateCurrent(
        { ...state, conversationId: action.conversationId },
        (turn) => ({ ...turn, requestId: action.requestId }),
      )
    case 'progress':
      return updateCurrent(state, (turn) => ({
        ...turn,
        progress: [...turn.progress, action.progress],
      }))
    case 'delta':
      return updateCurrent(state, (turn) => ({
        ...turn,
        status: 'streaming',
        answer: turn.answer + action.delta,
      }))
    case 'completed':
      return updateCurrent(state, (turn) => ({
        ...turn,
        status: 'succeeded',
        answer: action.result.answer,
        result: action.result,
        requestId: action.result.request_id,
      }))
    case 'failed':
      return updateCurrent(state, (turn) => ({
        ...turn,
        status: 'failed',
        error: action.error,
      }))
    case 'cancelled':
      return updateCurrent(state, (turn) => ({
        ...turn,
        status: 'cancelled',
      }))
    case 'reset':
      return initialChatState
    default:
      return state
  }
}
