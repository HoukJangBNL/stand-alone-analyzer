// web/src/api/sseRun.ts
/**
 * Shared POST helper for SSE-streaming run endpoints. Returns the raw Response
 * (caller pipes it through parseEventStream). Throws ApiError on non-2xx where
 * the server actually returned a JSON envelope; otherwise throws a plain Error
 * with the HTTP status — SSE error envelopes are surfaced via the event stream
 * itself, not the response status.
 */
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export async function postSseRun(
  projectId: string,
  scanId: string | number,
  step: string,
  body: unknown,
  signal: AbortSignal
): Promise<Response> {
  const response = await fetch(
    `/api/v1/projects/${projectId}/scans/${scanId}/run/${step}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...getAuthHeaders() },
      credentials: 'include',
      body: JSON.stringify(body),
      signal,
    }
  )
  if (!response.ok) {
    let envelope: {
      error?: {
        code?: string
        message?: string
        details?: unknown
        request_id?: string
      }
    } | null = null
    try {
      envelope = await response.clone().json()
    } catch {
      throw new Error(`HTTP ${response.status}`)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      response.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${response.status}`,
      err.details ?? null,
      err.request_id
    )
  }
  return response
}
