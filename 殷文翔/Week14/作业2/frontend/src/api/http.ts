import type { ErrorResponse, HealthResponse } from '../types/common'

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(public status: number, public body: ErrorResponse) { super(body.error.message) }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${baseUrl}${path}`, { ...options, headers })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as ErrorResponse | null
    throw new ApiError(response.status, payload?.error ? payload : {
      request_id: response.headers.get('X-Request-ID') || '',
      error: { code: 'HTTP_ERROR', message: `请求失败 (${response.status})`, retryable: response.status >= 500, details: {} },
    })
  }
  return await response.json() as T
}

export const getHealth = (signal?: AbortSignal) => request<HealthResponse>('/health', { signal })
