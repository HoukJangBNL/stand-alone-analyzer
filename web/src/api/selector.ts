// web/src/api/selector.ts
/**
 * Typed fetch wrappers for the Selector endpoints.
 *
 * Pulls every error envelope into a thrown ApiError to keep TanStack Query's
 * onError / isError contract clean.
 */

import type { SelectorApiParams } from '@/state/selectorSlice'

export class ApiError extends Error {
  code: string
  details: unknown
  status: number
  constructor(status: number, code: string, message: string, details: unknown) {
    super(`[${code}] ${message}`)
    this.code = code
    this.details = details
    this.status = status
  }
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: any = null
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
      err.details ?? null
    )
  }
  return (await resp.json()) as T
}

export interface DomainStats {
  flake_ids: number[]
  mean_r: number[]
  mean_g: number[]
  mean_b: number[]
  std_r: number[]
  std_g: number[]
  std_b: number[]
  areas: number[]
  sam2?: number[]
}

export async function fetchDomainStats(projectId: string): Promise<DomainStats> {
  const resp = await fetch(`/api/v1/projects/${projectId}/data/domain_stats`, {
    headers: { Accept: 'application/json' },
  })
  return unwrap<DomainStats>(resp)
}

export interface SelectionRows {
  domain_id: number[]
  selected: boolean[]
}

export async function fetchSelection(projectId: string): Promise<SelectionRows> {
  const resp = await fetch(`/api/v1/projects/${projectId}/data/selector/selection`, {
    headers: { Accept: 'application/json' },
  })
  return unwrap<SelectionRows>(resp)
}

export interface CommitRequest {
  params: SelectorApiParams
  lasso_ids: number[] | null
}

export interface CommitSummary {
  output_path: string
  n_committed: number
  n_filter_accepted: number
  n_lasso: number
  total_count: number
  params_hash: string | null
}

export async function postCommit(
  projectId: string,
  body: CommitRequest
): Promise<CommitSummary> {
  const resp = await fetch(`/api/v1/projects/${projectId}/selector/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return unwrap<CommitSummary>(resp)
}

export function buildPreviewUrl(
  projectId: string,
  domainId: number,
  withContour: boolean
): string {
  return `/api/v1/projects/${projectId}/data/annotations/${domainId}/preview?with_contour=${withContour}`
}

export function buildExportUrl(
  projectId: string,
  mode: 'filtered' | 'selected'
): string {
  return `/api/v1/projects/${projectId}/selector/export?mode=${mode}`
}
