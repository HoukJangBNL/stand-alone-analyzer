// web/src/api/gpu.ts
/**
 * Typed fetch wrapper for `GET /api/v1/gpu/status` (used by ComputeTab's
 * GpuPoolBadge). Five-state availability classification of the SAM GPU
 * spot pool — see src/flake_analysis/api/schemas/gpu.py for the source
 * of truth.
 */
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export type GpuPoolState =
  | 'ready'
  | 'launching'
  | 'unavailable_capacity'
  | 'running'
  | 'unknown'

export interface GpuPoolStatus {
  state: GpuPoolState
  detail: string
  checked_at: string
  spot_prices_usd_per_hr: Record<string, number> | null
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: {
      error?: { code?: string; message?: string; details?: unknown; request_id?: string }
    } | null = null
    try {
      envelope = await resp.json()
    } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id
    )
  }
  return (await resp.json()) as T
}

export async function fetchGpuStatus(): Promise<GpuPoolStatus> {
  const resp = await fetch('/api/v1/gpu/status', {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  return unwrap<GpuPoolStatus>(resp)
}
