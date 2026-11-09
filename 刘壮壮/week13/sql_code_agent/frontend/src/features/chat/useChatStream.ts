import { useCallback, useEffect, useReducer, useRef } from 'react'
import { streamChat } from '../../shared/api/chatStream'
import type { ApiError, ChatStreamEvent } from '../../shared/types/api'
import {
  chatReducer,
  conversationHistory,
  initialChatState,
  type ChatState,
} from './chatReducer'

interface UseChatStream {
  state: ChatState
  submit: (question: string) => Promise<void>
  cancel: () => void
  reset: () => void
}

export function useChatStream(): UseChatStream {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      controllerRef.current?.abort()
    },
    [],
  )

  const submit = useCallback(async (rawQuestion: string) => {
    const question = rawQuestion.trim()
    if (!question) return

    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const history = conversationHistory(state)

    dispatch({
      type: 'started',
      question,
      turnId: crypto.randomUUID(),
    })
    const terminalEvents = new Set<'result' | 'error'>()

    const handleEvent = (event: ChatStreamEvent) => {
      switch (event.type) {
        case 'meta':
          dispatch({
            type: 'meta',
            requestId: event.data.request_id,
            conversationId: event.data.conversation_id,
          })
          break
        case 'progress':
          dispatch({ type: 'progress', progress: event.data })
          break
        case 'answer_delta':
          dispatch({ type: 'delta', delta: event.data.delta })
          break
        case 'result':
          terminalEvents.add('result')
          dispatch({ type: 'completed', result: event.data })
          break
        case 'error':
          terminalEvents.add('error')
          dispatch({ type: 'failed', error: event.data })
          break
      }
    }

    try {
      await streamChat(
        {
          question,
          conversation_id: state.conversationId,
          history,
        },
        controller.signal,
        handleEvent,
      )
      if (terminalEvents.size === 0) {
        throw new Error('响应流提前结束，请重试。')
      }
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: 'cancelled' })
      } else {
        const fallback: ApiError = {
          request_id: '',
          code: 'NetworkError',
          message:
            error instanceof Error ? error.message : '网络连接异常，请重试。',
        }
        dispatch({ type: 'failed', error: fallback })
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
      }
    }
  }, [state])

  const cancel = useCallback(() => {
    controllerRef.current?.abort()
  }, [])

  const reset = useCallback(() => {
    controllerRef.current?.abort()
    dispatch({ type: 'reset' })
  }, [])

  return { state, submit, cancel, reset }
}
