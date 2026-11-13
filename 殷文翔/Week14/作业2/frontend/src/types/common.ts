export interface ErrorDetail {
  code: string
  message: string
  retryable: boolean
  details: Record<string, unknown>
}
export interface ErrorResponse { error: ErrorDetail; request_id: string }
export interface Page<T> { items: T[]; page: number; page_size: number; total: number }
export interface HealthResponse { status: string; service: string; scope: string }
export interface Capabilities {
  vision_enabled: boolean
  upload_max_files: number
  upload_max_file_bytes: number
  upload_max_request_bytes: number
  image_max_upload_files: number
  image_max_file_bytes: number
}
